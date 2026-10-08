import csv
import io
import json
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from openpyxl import load_workbook
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import IdempotencyRecord, Tenant, User
from accounts.serializers import LoginSerializer, TenantSerializer, UserSerializer
from accounts.viewsets import RoleProtectedTenantViewSet, TenantScopedModelViewSet
from ai_services.complaint_ai import analyze_complaint
from assistance.models import Beneficiary, CashEntitlement, Enrollment, Household, HouseholdEligibility, HouseholdEnrollment
from assistance.serializer_helpers import national_id_digest, national_id_reference, normalize_national_id, normalize_phone_number
from assistance.serializers import BeneficiarySerializer, CashEntitlementSerializer, EnrollmentSerializer, HouseholdEligibilitySerializer, HouseholdEnrollmentSerializer, HouseholdSerializer
from automation.models import AISignal, AutomationExecution, AutomationRule, ReviewTask
from automation.serializers import AISignalSerializer, AutomationExecutionSerializer, AutomationRuleSerializer, ReviewTaskSerializer
from operations.inventory import _available_nfi_stock, _nfi_reserved_quantity, _reserve_nfi_entitlement_stock
from operations.models import Complaint, ComplaintAIAnalysis, DistributionAllocation, DistributionEvent, DistributionIssue, NFIEntitlement, NFIItem, PaymentDeliveryReview, StockMovement, Warehouse
from operations.serializers import ComplaintAIAnalysisSerializer, ComplaintSerializer, DistributionAllocationSerializer, DistributionEventSerializer, DistributionIssueSerializer, NFIEntitlementSerializer, NFIItemSerializer, PaymentDeliveryReviewSerializer, StockMovementSerializer, WarehouseSerializer
from payments.models import PaymentBatch, PaymentChannelConfig, PaymentEvent, PaymentInstruction, ReconciliationItem
from payments.serializers import PaymentBatchSerializer, PaymentChannelConfigSerializer, PaymentEventSerializer, PaymentInstructionSerializer, ReconciliationItemSerializer
from programs.models import ActivityDependency, Budget, Program, ProgramActivity
from programs.serializers import ActivityDependencySerializer, BudgetSerializer, ProgramActivitySerializer, ProgramSerializer
from programs.services import payment_budget_totals, scoped_program
from reporting.audit import audit, json_safe
from reporting.import_utils import normalize_import_header, normalize_import_rows
from reporting.models import AuditEvent, PDMResponse
from reporting.serializers import AuditEventSerializer
from services.platform import AI_AUTOMATION_ENABLED, run_anomaly_detection, run_automated_reconciliation, run_deduplication_check, verified_program_summary



class PaymentInstructionViewSet(RoleProtectedTenantViewSet):
    queryset = PaymentInstruction.objects.select_related(
        "cash_entitlement__program",
        "cash_entitlement__household",
        "enrollment__program",
        "beneficiary",
        "channel_config",
    )
    serializer_class = PaymentInstructionSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR, User.Role.REVIEWER}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}

    def get_queryset(self):
        queryset = self.queryset.filter(
            Q(cash_entitlement__program__cash_enabled=True)
            | Q(enrollment__program__cash_enabled=True)
        )
        if not self.request.user.is_superuser:
            queryset = queryset.filter(
                Q(cash_entitlement__program__tenant=self.request.user.tenant)
                | Q(enrollment__program__tenant=self.request.user.tenant)
            )
        return queryset

    def update(self, request, *args, **kwargs):
        raise PermissionDenied("Payment instructions are immutable; use the controlled simulator actions")

    def partial_update(self, request, *args, **kwargs):
        raise PermissionDenied("Payment instructions are immutable; use the controlled simulator actions")

    @transaction.atomic
    def perform_create(self, serializer):
        data = serializer.validated_data
        entitlement = data.get("cash_entitlement")
        household = data["household"] if not entitlement else entitlement.household
        program = Program.objects.select_for_update().get(pk=(entitlement.program_id if entitlement else household.program_id))
        if not Budget.objects.select_for_update().filter(program=program).exists():
            raise ValidationError({"program": "A program budget is required before creating a payment instruction"})
        channel = PaymentChannelConfig.objects.select_for_update().get(
            pk=data["channel_config"].pk
        )
        batch = data.get("batch")
        if batch:
            batch = PaymentBatch.objects.select_for_update().get(pk=batch.pk)
        if entitlement and entitlement.status != CashEntitlement.Status.ACTIVE:
            raise ValidationError({"cash_entitlement": "Only active cash entitlements can be paid"})
        if not program.cash_enabled or not program.workflow_config.get("payment_enabled", True):
            raise ValidationError({"cash_entitlement": "Payments are disabled for this program"})
        if not self.request.user.is_superuser and program.tenant_id != self.request.user.tenant_id:
            raise ValidationError({"cash_entitlement": "Cash entitlement belongs to another tenant"})
        if channel.program_id != program.id or not channel.is_active or channel.currency != program.currency:
            raise ValidationError({"channel_config": "An active channel with the program currency is required"})
        if entitlement and (entitlement.currency != program.currency or entitlement.currency != channel.currency):
            raise ValidationError({"amount": "Payment amount and currency must match the cash entitlement and program"})
        financials = payment_budget_totals(program)
        requested_amount = entitlement.amount if entitlement else data["amount"]
        if requested_amount + channel.fee_amount > financials["remaining"]:
            raise ValidationError({"amount": f"Insufficient program budget. Remaining: {financials['remaining']} {program.currency}."})
        if batch and (batch.tenant_id != program.tenant_id or batch.program_id != program.id):
            raise ValidationError({"batch": "Payment batch must belong to the entitlement program and tenant"})
        if not HouseholdEligibility.objects.select_for_update().filter(
            household=household,
            program=program,
            status=HouseholdEligibility.Status.ELIGIBLE,
        ).exists() or not HouseholdEnrollment.objects.select_for_update().filter(
            household=household,
            program=program,
            status=HouseholdEnrollment.Status.APPROVED,
            assistance_modality__in=[
                Enrollment.AssistanceModality.CASH,
                Enrollment.AssistanceModality.CASH_NFI,
            ],
        ).exists():
            raise ValidationError({"cash_entitlement": "Payment instruction requires an approved household cash enrollment"})
        if entitlement and PaymentInstruction.objects.filter(cash_entitlement=entitlement).exists():
            raise ValidationError({"cash_entitlement": "A payment instruction already exists for this cash entitlement"})
        if not entitlement:
            entitlement = CashEntitlement.objects.create(
                household=household,
                program=program,
                amount=requested_amount,
                currency=data["currency"],
                status=CashEntitlement.Status.ACTIVE,
                created_by=self.request.user,
            )
        instruction = serializer.save(
            cash_entitlement=entitlement,
            channel_config=channel,
            batch=batch,
            enrollment=None,
            beneficiary=entitlement.beneficiary,
            amount=requested_amount,
            currency=data["currency"],
            applied_fee=channel.fee_amount,
            created_by=self.request.user,
            status=PaymentInstruction.Status.CREATED,
            idempotency_key=str(uuid.uuid4()),
        )
        PaymentEvent.objects.create(
            instruction=instruction,
            event_type=PaymentEvent.EventType.CREATED,
            to_status=PaymentInstruction.Status.CREATED,
            recorded_by=self.request.user,
            redacted_payload={
                "simulation": True,
                "fee_amount": str(channel.fee_amount),
                "total_financial_impact": str(entitlement.amount + channel.fee_amount),
            },
        )
        audit(
            self.request.user,
            "PAYMENT_INSTRUCTION_CREATED",
            instruction,
            after=serializer.data,
            tenant=program.tenant,
        )

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def simulate(self, request, pk=None):
        scoped = self.get_queryset().filter(pk=pk).values_list("pk", flat=True).first()
        if not scoped:
            raise ValidationError({"payment_instruction": "Payment instruction was not found"})
        instruction = PaymentInstruction.objects.select_for_update().get(pk=scoped)
        outcome = request.data.get("outcome")
        transitions = {
            "submit": (PaymentInstruction.Status.SUBMITTED, PaymentEvent.EventType.SUBMITTED),
            "success": (PaymentInstruction.Status.SUCCESS, PaymentEvent.EventType.SUCCESS),
            "failure": (PaymentInstruction.Status.FAILED, PaymentEvent.EventType.FAILED),
            "retry": (PaymentInstruction.Status.SUBMITTED, PaymentEvent.EventType.RETRY),
            "reversal": (PaymentInstruction.Status.REVERSED, PaymentEvent.EventType.REVERSED),
        }
        if outcome not in transitions:
            raise ValidationError({"outcome": "Use submit, success, failure, retry, or reversal"})
        old_status = instruction.status
        new_status, event_type = transitions[outcome]
        if outcome == "success" and old_status == PaymentInstruction.Status.SUCCESS:
            raise ValidationError({"outcome": "This payment already succeeded; its fee cannot be charged twice"})
        if new_status == PaymentInstruction.Status.REVERSED and old_status not in {PaymentInstruction.Status.SUCCESS, PaymentInstruction.Status.FAILED}:
            raise ValidationError({"outcome": "Only success or failed instructions can be reversed"})
        program = instruction.cash_entitlement.program
        if not Budget.objects.select_for_update().filter(program=program).exists():
            raise ValidationError({"outcome": "The payment program has no configured budget"})
        if outcome == "success":
            financials = payment_budget_totals(program)
            if instruction.applied_fee > financials["remaining"]:
                raise ValidationError({"outcome": f"Insufficient budget for the payment fee. Remaining: {financials['remaining']} {instruction.currency}."})
        instruction.status = new_status
        instruction.provider_reference = instruction.provider_reference or f"SIM-{str(instruction.id)[:8].upper()}"
        instruction.save(update_fields=["status", "provider_reference"])
        if outcome == "success" and instruction.cash_entitlement.status == CashEntitlement.Status.ACTIVE:
            instruction.cash_entitlement.status = CashEntitlement.Status.PAID
            instruction.cash_entitlement.save(update_fields=["status", "updated_at"])
        event = PaymentEvent.objects.create(
            instruction=instruction,
            event_type=event_type,
            provider_status=PaymentEvent.ProviderStatus.ACCEPTED if outcome in {"submit", "retry"} else PaymentEvent.ProviderStatus.SETTLED if outcome == "success" else PaymentEvent.ProviderStatus.FAILED,
            provider_transaction_id=request.data.get("provider_transaction_id", ""),
            from_status=old_status,
            to_status=new_status,
            recorded_by=request.user,
            redacted_payload={
                "simulation": True,
                "no_external_provider_call": True,
                "fee_amount": str(instruction.applied_fee),
                "total_financial_impact": str(instruction.amount + instruction.applied_fee),
            },
        )
        audit(
            request.user,
            "PAYMENT_SIMULATED",
            instruction,
            before={"status": old_status},
            after={"status": new_status},
            tenant=(
                instruction.cash_entitlement.program.tenant
                if instruction.cash_entitlement_id
                else instruction.enrollment.program.tenant
            ),
        )
        return Response(PaymentEventSerializer(event).data)

    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        return Response(PaymentEventSerializer(self.get_object().events.all().order_by("created_at"), many=True).data)


class PaymentBatchViewSet(RoleProtectedTenantViewSet):
    queryset = PaymentBatch.objects.select_related("program")
    serializer_class = PaymentBatchSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}

    def get_queryset(self):
        return super().get_queryset().filter(program__cash_enabled=True)

    def perform_create(self, serializer):
        program = serializer.validated_data["program"]
        if not self.request.user.is_superuser and program.tenant_id != self.request.user.tenant_id:
            raise ValidationError({"program": "Program belongs to another tenant"})
        if not program.cash_enabled:
            raise ValidationError({"program": "Cash assistance is disabled for this program"})
        batch = serializer.save(tenant=program.tenant, created_by=self.request.user, idempotency_key=str(uuid.uuid4()))
        audit(self.request.user, "PAYMENT_BATCH_CREATED", batch, after=PaymentBatchSerializer(batch).data, tenant=program.tenant)


class ReconciliationItemViewSet(RoleProtectedTenantViewSet):
    queryset = ReconciliationItem.objects.select_related("program", "instruction")
    serializer_class = ReconciliationItemSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}

    def create(self, request, *args, **kwargs):
        raise PermissionDenied("Reconciliation items are created only by the reconciliation service")

    def update(self, request, *args, **kwargs):
        raise PermissionDenied("Use the controlled reconciliation resolution action")

    def partial_update(self, request, *args, **kwargs):
        raise PermissionDenied("Use the controlled reconciliation resolution action")

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        item = self.get_object()
        item.status = ReconciliationItem.Status.RESOLVED
        item.resolution_note = request.data.get("resolution_note", "")
        item.resolved_by = request.user
        item.resolved_at = timezone.now()
        item.save(update_fields=["status", "resolution_note", "resolved_by", "resolved_at"])
        audit(request.user, "RECONCILIATION_ITEM_RESOLVED", item, after={"status": item.status}, tenant=item.tenant)
        return Response(ReconciliationItemSerializer(item).data)
