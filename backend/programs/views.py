import csv
import io
import json
import uuid
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256

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



class ProgramViewSet(RoleProtectedTenantViewSet):
    queryset = Program.objects.all()
    serializer_class = ProgramSerializer
    allowed_roles = {
        User.Role.ADMIN,
        User.Role.FINANCE,
        User.Role.MANAGER,
        User.Role.FIELD_OFFICER,
        User.Role.REVIEWER,
        User.Role.SUPPORT,
        User.Role.AUDITOR,
    }
    write_roles = {User.Role.ADMIN, User.Role.MANAGER}

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        program = self.get_object()
        household_ids = list(program.households.values_list("id", flat=True))
        beneficiary_ids = list(Beneficiary.objects.filter(household_id__in=household_ids).values_list("id", flat=True))
        enrollment_ids = list(Enrollment.objects.filter(program=program).values_list("id", flat=True))
        instruction_ids = list(
            PaymentInstruction.objects.filter(
                Q(enrollment_id__in=enrollment_ids) | Q(cash_entitlement__program=program)
            ).values_list("id", flat=True)
        )
        activity_ids = list(program.activities.values_list("id", flat=True))
        rule_ids = list(program.automation_rules.values_list("id", flat=True))
        audit(request.user, "PROGRAM_DELETED", program, after={"deleted_dependency_counts": {"households": len(household_ids), "beneficiaries": len(beneficiary_ids), "enrollments": len(enrollment_ids), "payment_instructions": len(instruction_ids), "activities": len(activity_ids), "automation_rules": len(rule_ids)}}, tenant=program.tenant)
        Complaint.objects.filter(Q(beneficiary_id__in=beneficiary_ids) | Q(instruction_id__in=instruction_ids)).delete()
        PaymentDeliveryReview.objects.filter(payment_instruction_id__in=instruction_ids).delete()
        PaymentEvent.objects.filter(instruction_id__in=instruction_ids).delete()
        ReconciliationItem.objects.filter(Q(program=program) | Q(instruction_id__in=instruction_ids)).delete()
        PaymentInstruction.objects.filter(id__in=instruction_ids).delete()
        # Remove additive assistance records before their protected beneficiaries,
        # items, warehouses, and program can be removed.
        program_issue_ids = list(DistributionIssue.objects.filter(entitlement__program=program).values_list("id", flat=True))
        DistributionIssue.objects.filter(id__in=program_issue_ids).delete()
        DistributionAllocation.objects.filter(entitlement__program=program).delete()
        NFIEntitlement.objects.filter(program=program).delete()
        DistributionEvent.objects.filter(program=program).delete()
        StockMovement.objects.filter(program=program).delete()
        NFIItem.objects.filter(program=program).delete()
        Warehouse.objects.filter(program=program).delete()
        CashEntitlement.objects.filter(program=program).delete()
        HouseholdEnrollment.objects.filter(program=program).delete()
        HouseholdEligibility.objects.filter(program=program).delete()
        Enrollment.objects.filter(id__in=enrollment_ids).delete()
        Beneficiary.objects.filter(id__in=beneficiary_ids).delete()
        Household.objects.filter(id__in=household_ids).delete()
        ActivityDependency.objects.filter(Q(predecessor_id__in=activity_ids) | Q(successor_id__in=activity_ids)).delete()
        ProgramActivity.objects.filter(id__in=activity_ids).delete()
        AutomationExecution.objects.filter(rule_id__in=rule_ids).delete()
        AutomationRule.objects.filter(id__in=rule_ids).delete()
        AISignal.objects.filter(program=program).delete()
        ReviewTask.objects.filter(program=program).delete()
        PDMResponse.objects.filter(program=program).delete()
        PaymentChannelConfig.objects.filter(program=program).delete()
        PaymentBatch.objects.filter(program=program).delete()
        Budget.objects.filter(program=program).delete()
        program.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def initial(self, request, *args, **kwargs):
        TenantScopedModelViewSet.initial(self, request, *args, **kwargs)
        if request.user.role not in self.allowed_roles:
            raise PermissionDenied("Your role does not have permission for this resource")
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return
        if self.action == "channels" and request.user.role in {
            User.Role.ADMIN,
            User.Role.MANAGER,
            User.Role.FINANCE,
        }:
            return
        if request.user.role not in self.write_roles:
            raise PermissionDenied("Your role has read-only access to this resource")

    @action(detail=True, methods=["get", "post"], url_path="channels")
    @transaction.atomic
    def channels(self, request, pk=None):
        program = self.get_object()
        if not program.cash_enabled:
            raise ValidationError("Cash assistance is disabled for this program")
        if request.method == "GET":
            return Response(PaymentChannelConfigSerializer(program.channels.all(), many=True, context={"request": request}).data)
        key = request.headers.get("Idempotency-Key", "").strip()
        fingerprint = sha256(json.dumps(request.data, sort_keys=True, default=str).encode("utf-8")).hexdigest() if key else ""
        record = None
        if key:
            record, created = IdempotencyRecord.objects.get_or_create(
                tenant=request.user.tenant,
                actor=request.user,
                endpoint=request.path,
                idempotency_key=key,
                defaults={"request_fingerprint": fingerprint, "response_body": {}},
            )
            record = IdempotencyRecord.objects.select_for_update().get(pk=record.pk)
            if not created:
                if record.request_fingerprint != fingerprint:
                    raise ValidationError({"idempotency_key": "This idempotency key was already used with different data."})
                return Response(record.response_body, status=record.response_status)
        serializer = PaymentChannelConfigSerializer(
            data={**request.data, "program": str(program.id)},
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        channel = serializer.save()
        audit(request.user, "PAYMENT_CHANNEL_CONFIG_CREATED", channel, after=serializer.data, tenant=program.tenant)
        if channel.fee_amount:
            audit(request.user, "PAYMENT_CHANNEL_FEE_CONFIGURED", channel, after={"fee_amount": str(channel.fee_amount), "currency": channel.currency}, tenant=program.tenant)
        response = Response(serializer.data, status=status.HTTP_201_CREATED)
        if record is not None:
            record.response_status = response.status_code
            record.response_body = json_safe(response.data)
            record.save(update_fields=["response_status", "response_body"])
        return response


class ProgramActivityViewSet(RoleProtectedTenantViewSet):
    queryset = ProgramActivity.objects.select_related("program", "owner")
    serializer_class = ProgramActivitySerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.FIELD_OFFICER, User.Role.REVIEWER, User.Role.FINANCE, User.Role.SUPPORT, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER}
    tenant_field = "tenant"

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.role in {User.Role.FIELD_OFFICER, User.Role.REVIEWER, User.Role.FINANCE, User.Role.SUPPORT}:
            return queryset.filter(owner=self.request.user)
        return queryset

    def perform_create(self, serializer):
        program = serializer.validated_data["program"]
        if not self.request.user.is_superuser and program.tenant_id != self.request.user.tenant_id:
            raise ValidationError({"program": "Program belongs to another tenant"})
        instance = serializer.save(tenant=program.tenant, created_by=self.request.user)
        audit(self.request.user, "PROGRAM_ACTIVITY_CREATED", instance, after=ProgramActivitySerializer(instance).data, tenant=program.tenant)


class ActivityDependencyViewSet(RoleProtectedTenantViewSet):
    queryset = ActivityDependency.objects.select_related("predecessor", "successor")
    serializer_class = ActivityDependencySerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER}

    def get_queryset(self):
        if self.request.user.is_superuser:
            return self.queryset.all()
        return self.queryset.filter(predecessor__tenant=self.request.user.tenant)

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        audit(self.request.user, "ACTIVITY_DEPENDENCY_CREATED", instance, after=serializer.data, tenant=instance.predecessor.tenant)


class BudgetViewSet(RoleProtectedTenantViewSet):
    queryset = Budget.objects.select_related("program")
    serializer_class = BudgetSerializer
    tenant_field = "program__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}
