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
from assistance.models import Beneficiary, CashEntitlement, Enrollment, Household, HouseholdEligibility, HouseholdEnrollment, HouseholdModalityDecision
from assistance.serializer_helpers import national_id_digest, national_id_reference, normalize_national_id, normalize_phone_number
from assistance.serializers import BeneficiarySerializer, CashEntitlementSerializer, EnrollmentSerializer, HouseholdEligibilitySerializer, HouseholdEnrollmentSerializer, HouseholdModalityDecisionSerializer, HouseholdSerializer
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
from programs.services import require_program_active, require_reason
from medical.models import MedicalDistributionAllocation, MedicalDistributionIssue, MedicalEntitlement
from medical.services import cancel_entitlement as cancel_medical_entitlement
from reporting.audit import audit, json_safe
from payments.cash_validation import validate_cash_amount_or_raise
from reporting.import_utils import normalize_import_header, normalize_import_rows
from reporting.models import AuditEvent, PDMResponse
from reporting.serializers import AuditEventSerializer



# Registers households and returns only records visible to the current tenant.
class HouseholdViewSet(RoleProtectedTenantViewSet):
    queryset = Household.objects.select_related("program").order_by("-created_at", "id")
    serializer_class = HouseholdSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.REVIEWER, User.Role.MANAGER, User.Role.FINANCE}
    write_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.MANAGER}

    def get_queryset(self):
        queryset = super().get_queryset()
        program_id = self.request.query_params.get("program")
        if program_id:
            queryset = queryset.filter(program_id=program_id)
        return queryset

    @transaction.atomic
    def perform_create(self, serializer):
        program = Program.objects.select_for_update().get(pk=serializer.validated_data["program"].pk)
        require_program_active(program, "household registration")
        serializer.validated_data["program"] = program
        super().perform_create(serializer)

    @transaction.atomic
    def perform_update(self, serializer):
        household = Household.objects.select_for_update().get(pk=serializer.instance.pk)
        household_size = serializer.validated_data.get("household_size", household.household_size)
        member_count = household.beneficiaries.count()
        if household_size < member_count:
            raise ValidationError(
                {
                    "household_size": (
                        f"Household size cannot be less than its current "
                        f"{member_count} beneficiaries."
                    )
                }
            )
        serializer.instance = household
        super().perform_update(serializer)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def withdraw(self, request, pk=None):
        if request.user.role not in {User.Role.ADMIN, User.Role.MANAGER}:
            raise PermissionDenied("Only administrators and managers can withdraw a household")
        reason = require_reason(request.data.get("reason"))
        household = Household.objects.select_for_update().select_related("program", "tenant").get(pk=self.get_object().pk)
        Program.objects.select_for_update().get(pk=household.program_id)
        if household.participation_status == Household.ParticipationStatus.WITHDRAWN:
            return Response(self.get_serializer(household).data)

        summary = {"cash_cancelled": 0, "nfi_cancelled": 0, "medical_cancelled": 0, "history_preserved": 0}
        irreversible = {
            PaymentInstruction.Status.SUBMITTED,
            PaymentInstruction.Status.FAILED,
            PaymentInstruction.Status.SUCCESS,
            PaymentInstruction.Status.REVERSED,
        }
        for entitlement in CashEntitlement.objects.select_for_update().filter(
            household=household, status=CashEntitlement.Status.ACTIVE
        ):
            instructions = PaymentInstruction.objects.select_for_update().filter(cash_entitlement=entitlement)
            if instructions.filter(status__in=irreversible).exists():
                summary["history_preserved"] += 1
                continue
            entitlement.status = CashEntitlement.Status.CANCELLED
            entitlement.cancellation_reason = reason
            entitlement.cancelled_by = request.user
            entitlement.cancelled_at = timezone.now()
            entitlement.save(update_fields=["status", "cancellation_reason", "cancelled_by", "cancelled_at", "updated_at"])
            for instruction in instructions.filter(status__in=[PaymentInstruction.Status.DRAFT, PaymentInstruction.Status.CREATED]):
                previous = instruction.status
                instruction.status = PaymentInstruction.Status.CANCELLED
                instruction.save(update_fields=["status"])
                PaymentEvent.objects.create(
                    instruction=instruction,
                    event_type=PaymentEvent.EventType.CANCELLED,
                    from_status=previous,
                    to_status=PaymentInstruction.Status.CANCELLED,
                    recorded_by=request.user,
                    redacted_payload={"reason": reason},
                )
            summary["cash_cancelled"] += 1

        for entitlement in NFIEntitlement.objects.select_for_update().filter(
            household=household, status=NFIEntitlement.Status.ACTIVE
        ):
            if entitlement.issues.filter(actual_quantity__gt=0).exists():
                summary["history_preserved"] += 1
                continue
            entitlement.allocations.exclude(status="CANCELLED").update(status="CANCELLED")
            item = NFIItem.objects.select_for_update().get(pk=entitlement.item_id)
            item.available_quantity += entitlement.quantity
            item.save(update_fields=["available_quantity", "updated_at"])
            entitlement.status = NFIEntitlement.Status.CANCELLED
            entitlement.save(update_fields=["status", "updated_at"])
            StockMovement.objects.create(
                program=entitlement.program,
                warehouse=entitlement.warehouse,
                item=item,
                quantity=entitlement.quantity,
                movement_type=StockMovement.MovementType.RETURN,
                reference=str(entitlement.pk),
                created_by=request.user,
            )
            summary["nfi_cancelled"] += 1

        for entitlement in MedicalEntitlement.objects.select_for_update().filter(
            household=household,
            status__in=[MedicalEntitlement.Status.PENDING, MedicalEntitlement.Status.APPROVED, MedicalEntitlement.Status.ALLOCATED],
        ):
            if MedicalDistributionIssue.objects.filter(allocation__entitlement=entitlement, actual_quantity__gt=0).exists():
                summary["history_preserved"] += 1
                continue
            entitlement.distribution_allocations.exclude(
                status=MedicalDistributionAllocation.Status.CANCELLED
            ).update(status=MedicalDistributionAllocation.Status.CANCELLED)
            cancel_medical_entitlement(entitlement.pk, request.user, reason)
            summary["medical_cancelled"] += 1

        HouseholdEnrollment.objects.filter(household=household).exclude(
            status=HouseholdEnrollment.Status.SUSPENDED
        ).update(status=HouseholdEnrollment.Status.SUSPENDED, note=reason, decided_by=request.user, decided_at=timezone.now())
        HouseholdModalityDecision.objects.filter(household=household).exclude(
            status=HouseholdModalityDecision.Status.SUSPENDED
        ).update(status=HouseholdModalityDecision.Status.SUSPENDED, note=reason, decided_by=request.user, decided_at=timezone.now())
        Enrollment.objects.filter(Q(household=household) | Q(beneficiary__household=household)).update(
            status=Enrollment.Status.SUSPENDED
        )
        household.participation_status = Household.ParticipationStatus.WITHDRAWN
        household.withdrawal_reason = reason
        household.withdrawn_by = request.user
        household.withdrawn_at = timezone.now()
        household.save(update_fields=["participation_status", "withdrawal_reason", "withdrawn_by", "withdrawn_at"])
        audit(
            request.user,
            "HOUSEHOLD_WITHDRAWN_FROM_PROGRAM",
            household,
            after={"reason": reason, **summary},
            tenant=household.tenant,
        )
        return Response(self.get_serializer(household).data)


# Registers household members and handles controlled beneficiary exit.
class BeneficiaryViewSet(RoleProtectedTenantViewSet):
    queryset = Beneficiary.objects.select_related("household", "household__tenant").order_by("-created_at", "id")
    serializer_class = BeneficiarySerializer
    tenant_field = "household__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.REVIEWER, User.Role.SUPPORT, User.Role.MANAGER, User.Role.AUDITOR, User.Role.FINANCE}
    write_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.MANAGER}

    def get_queryset(self):
        queryset = super().get_queryset()
        program_id = self.request.query_params.get("program")
        search = self.request.query_params.get("search", "").strip()
        if program_id:
            queryset = queryset.filter(household__program_id=program_id)
        if search:
            queryset = queryset.filter(Q(full_name__icontains=search) | Q(number__icontains=search))
        return queryset

    @transaction.atomic
    def perform_create(self, serializer):
        household = Household.objects.select_for_update().get(
            pk=serializer.validated_data["household"].pk
        )
        require_program_active(household.program, "beneficiary registration")
        if household.participation_status != Household.ParticipationStatus.ACTIVE:
            raise ValidationError({"household": "This household has been withdrawn from the program."})
        member_count = household.beneficiaries.count()
        if member_count >= household.household_size:
            raise ValidationError(
                {
                    "household": (
                        f"This household already has {member_count} beneficiaries "
                        f"(maximum {household.household_size})."
                    )
                }
            )
        serializer.validated_data["household"] = household
        super().perform_create(serializer)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        raise PermissionDenied("Beneficiary deletion is disabled to preserve assistance and audit history; use the exit action.")

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def exit(self, request, pk=None):
        if request.user.role not in {User.Role.ADMIN, User.Role.MANAGER}:
            raise PermissionDenied("Only administrators and managers can exit beneficiaries")
        reason = require_reason(request.data.get("reason"))
        beneficiary = Beneficiary.objects.select_for_update().select_related("household__tenant").get(pk=self.get_object().pk)
        if beneficiary.status == Beneficiary.Status.EXITED:
            return Response(self.get_serializer(beneficiary).data)
        before = self.get_serializer(beneficiary).data
        beneficiary.status = Beneficiary.Status.EXITED
        beneficiary.save(update_fields=["status"])
        Enrollment.objects.filter(beneficiary=beneficiary).exclude(status=Enrollment.Status.SUSPENDED).update(status=Enrollment.Status.SUSPENDED)
        audit(request.user, "BENEFICIARY_EXITED", beneficiary, before=before, after={**self.get_serializer(beneficiary).data, "reason": reason}, tenant=beneficiary.household.tenant)
        return Response(self.get_serializer(beneficiary).data)


# Handles beneficiary enrollment and approval decisions.
class EnrollmentViewSet(RoleProtectedTenantViewSet):
    queryset = Enrollment.objects.select_related("program", "beneficiary", "beneficiary__household")
    serializer_class = EnrollmentSerializer
    tenant_field = "program__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER}


# Creates and updates human eligibility decisions for households.
class HouseholdEligibilityViewSet(RoleProtectedTenantViewSet):
    queryset = HouseholdEligibility.objects.select_related("household", "program")
    serializer_class = HouseholdEligibilitySerializer
    allowed_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER}
    tenant_field = "household__tenant"

    @transaction.atomic
    def perform_create(self, serializer):
        household = serializer.validated_data["household"]
        program = Program.objects.select_for_update().get(pk=household.program_id)
        require_program_active(program, "eligibility decisions")
        if household.participation_status != Household.ParticipationStatus.ACTIVE:
            raise ValidationError({"household": "This household has been withdrawn from the program."})
        instance = serializer.save(program=household.program, decided_by=self.request.user, decided_at=timezone.now())
        audit(self.request.user, "HOUSEHOLD_ELIGIBILITY_DECISION", instance, after=serializer.data)


class HouseholdEnrollmentViewSet(RoleProtectedTenantViewSet):
    queryset = HouseholdEnrollment.objects.select_related("household", "program").order_by("-created_at", "id")
    serializer_class = HouseholdEnrollmentSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER, User.Role.AUDITOR, User.Role.FINANCE}
    write_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER}
    tenant_field = "household__tenant"

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.role == User.Role.FINANCE:
            queryset = queryset.filter(
                status__in=[HouseholdEnrollment.Status.ACCEPTED, HouseholdEnrollment.Status.APPROVED]
            )
        return queryset

    @transaction.atomic
    def perform_create(self, serializer):
        household = serializer.validated_data["household"]
        program = serializer.validated_data.get("program") or household.program
        program = Program.objects.select_for_update().get(pk=program.pk)
        require_program_active(program, "enrollment decisions")
        if household.participation_status != Household.ParticipationStatus.ACTIVE:
            raise ValidationError({"household": "This household has been withdrawn from the program."})
        if not HouseholdEligibility.objects.filter(household=household, program=program, status=HouseholdEligibility.Status.ELIGIBLE).exists():
            raise ValidationError("A household must be eligible before enrollment")
        instance = serializer.save(program=program, decided_by=self.request.user, decided_at=timezone.now())
        if instance.status in {HouseholdEnrollment.Status.ACCEPTED, HouseholdEnrollment.Status.APPROVED}:
            Enrollment.objects.filter(beneficiary__household=household, program=program).update(status=Enrollment.Status.APPROVED, approved_by=self.request.user, approved_at=timezone.now(), household=household)
        audit(self.request.user, "HOUSEHOLD_ENROLLMENT_DECISION", instance, after=serializer.data)

    def perform_update(self, serializer):
        before = self.get_serializer(serializer.instance).data
        instance = serializer.save(decided_by=self.request.user, decided_at=timezone.now())
        legacy_enrollments = Enrollment.objects.filter(
            Q(household=instance.household) | Q(household__isnull=True, beneficiary__household=instance.household),
            program=instance.program,
        )
        if instance.status in {HouseholdEnrollment.Status.ACCEPTED, HouseholdEnrollment.Status.APPROVED}:
            legacy_enrollments.update(
                household=instance.household,
                status=Enrollment.Status.APPROVED,
                assistance_modality=instance.assistance_modality,
                approved_by=self.request.user,
                approved_at=timezone.now(),
            )
        audit(
            self.request.user,
            "HOUSEHOLD_ENROLLMENT_UPDATED",
            instance,
            before=before,
            after=self.get_serializer(instance).data,
        )


# Saves the approved assistance types before entitlements are created.
class HouseholdModalityDecisionViewSet(RoleProtectedTenantViewSet):
    queryset = HouseholdModalityDecision.objects.select_related("household", "program", "decided_by")
    serializer_class = HouseholdModalityDecisionSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER, User.Role.AUDITOR, User.Role.FINANCE}
    write_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER}
    tenant_field = "program__tenant"

    @transaction.atomic
    def perform_create(self, serializer):
        program = Program.objects.select_for_update().get(pk=serializer.validated_data["program"].pk)
        require_program_active(program, "modality decisions")
        instance = serializer.save(program=program, decided_by=self.request.user, decided_at=timezone.now())
        audit(self.request.user, "HOUSEHOLD_MODALITY_DECISION_CREATED", instance, after=self.get_serializer(instance).data)

    def perform_update(self, serializer):
        before = self.get_serializer(serializer.instance).data
        instance = serializer.save(decided_by=self.request.user, decided_at=timezone.now())
        audit(self.request.user, "HOUSEHOLD_MODALITY_DECISION_UPDATED", instance, before=before, after=self.get_serializer(instance).data)


# Creates cash entitlements for approved and eligible enrollments.
class CashEntitlementViewSet(RoleProtectedTenantViewSet):
    queryset = CashEntitlement.objects.select_related("household", "beneficiary", "program").order_by("-created_at", "id")
    serializer_class = CashEntitlementSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR, User.Role.REVIEWER}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}
    tenant_field = "program__tenant"

    def get_queryset(self):
        return super().get_queryset().filter(program__cash_enabled=True)

    def _preflight_cash_amount(self, request, instance=None):
        serializer = self.get_serializer(instance, data=request.data, partial=instance is not None and request.method == "PATCH")
        serializer.is_valid(raise_exception=True)
        program = serializer.validated_data.get("program", instance.program if instance else None)
        household = serializer.validated_data.get("household", instance.household if instance else None)
        amount = serializer.validated_data.get("amount", instance.amount if instance else None)
        validate_cash_amount_or_raise(
            program=program, household=household, amount=amount,
            entity_type="CASH_ENTITLEMENT",
            entity_id=str(instance.pk) if instance else f"cash-entitlement:{program.pk}:{household.pk}",
        )

    def create(self, request, *args, **kwargs):
        self._preflight_cash_amount(request)
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        self._preflight_cash_amount(request, self.get_object())
        return super().update(request, *args, **kwargs)

    def perform_create(self, serializer):
        self._save_with_budget_check(serializer, creating=True)

    def perform_update(self, serializer):
        self._save_with_budget_check(serializer, creating=False)

    @transaction.atomic
    def _save_with_budget_check(self, serializer, creating):
        program = serializer.validated_data.get("program", serializer.instance.program if serializer.instance else None)
        program = Program.objects.select_for_update().get(pk=program.pk)
        if creating:
            require_program_active(program, "cash entitlement creation")
        household = serializer.validated_data.get("household", serializer.instance.household if serializer.instance else None)
        if not creating:
            serializer.instance = CashEntitlement.objects.select_for_update().get(pk=serializer.instance.pk)
        budget = Budget.objects.select_for_update().filter(program=program).first()
        if not budget:
            raise ValidationError({"program": "A program budget is required before creating a cash entitlement."})
        requested = serializer.validated_data.get("amount", serializer.instance.amount if serializer.instance else Decimal("0"))
        committed = CashEntitlement.objects.select_for_update().filter(
            program=program,
            status__in=[CashEntitlement.Status.ACTIVE, CashEntitlement.Status.PAID],
        )
        if not creating:
            committed = committed.exclude(pk=serializer.instance.pk)
        committed_total = committed.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        irreversible_payment = PaymentInstruction.objects.select_for_update().filter(
            cash_entitlement=serializer.instance,
            status__in=[
                PaymentInstruction.Status.SUBMITTED,
                PaymentInstruction.Status.FAILED,
                PaymentInstruction.Status.SUCCESS,
                PaymentInstruction.Status.REVERSED,
            ],
        ).first() if not creating else None
        requested_status = serializer.validated_data.get(
            "status",
            serializer.instance.status if serializer.instance else CashEntitlement.Status.ACTIVE,
        )
        if irreversible_payment and (
            requested != serializer.instance.amount or requested_status == CashEntitlement.Status.CANCELLED
        ):
            raise ValidationError({"amount": "An entitlement cannot be changed after its payment has been submitted or produced an outcome."})
        if requested_status == CashEntitlement.Status.CANCELLED:
            requested = Decimal("0")
        successful_fees = PaymentInstruction.objects.select_for_update().filter(
            cash_entitlement__program=program,
            status=PaymentInstruction.Status.SUCCESS,
        ).aggregate(total=Sum("applied_fee"))["total"] or Decimal("0")
        available = budget.planned_total - committed_total - successful_fees
        if requested > available:
            raise ValidationError({"amount": f"Insufficient program budget. Available: {available} {budget.currency}; requested: {requested} {budget.currency}."})
        before = self.get_serializer(serializer.instance).data if serializer.instance else None
        instance = serializer.save(created_by=self.request.user) if creating else serializer.save()
        audit(
            self.request.user,
            "HOUSEHOLD_CASH_ENTITLEMENT_CREATED" if creating else (
                "HOUSEHOLD_CASH_ENTITLEMENT_CANCELLED"
                if instance.status == CashEntitlement.Status.CANCELLED
                else "HOUSEHOLD_CASH_ENTITLEMENT_UPDATED"
            ),
            instance,
            before=before,
            after={**self.get_serializer(instance).data, "budget": str(budget.planned_total), "committed": str(committed_total + requested), "remaining": str(available - requested)},
            tenant=program.tenant,
        )

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def cancel(self, request, pk=None):
        reason = require_reason(request.data.get("reason"))
        entitlement = CashEntitlement.objects.select_for_update().get(pk=self.get_object().pk)
        if entitlement.status == CashEntitlement.Status.CANCELLED:
            return Response(self.get_serializer(entitlement).data)
        if PaymentInstruction.objects.select_for_update().filter(
            cash_entitlement=entitlement,
            status__in=[
                PaymentInstruction.Status.SUBMITTED,
                PaymentInstruction.Status.FAILED,
                PaymentInstruction.Status.SUCCESS,
                PaymentInstruction.Status.REVERSED,
            ],
        ).exists():
            raise ValidationError({"status": "The cash entitlement is immutable after payment submission."})
        before = self.get_serializer(entitlement).data
        entitlement.status = CashEntitlement.Status.CANCELLED
        entitlement.cancellation_reason = reason
        entitlement.cancelled_by = request.user
        entitlement.cancelled_at = timezone.now()
        entitlement.save(update_fields=["status", "cancellation_reason", "cancelled_by", "cancelled_at", "updated_at"])
        for instruction in PaymentInstruction.objects.select_for_update().filter(
            cash_entitlement=entitlement,
            status__in=[PaymentInstruction.Status.DRAFT, PaymentInstruction.Status.CREATED],
        ):
            previous = instruction.status
            instruction.status = PaymentInstruction.Status.CANCELLED
            instruction.save(update_fields=["status"])
            PaymentEvent.objects.create(
                instruction=instruction,
                event_type=PaymentEvent.EventType.CANCELLED,
                from_status=previous,
                to_status=PaymentInstruction.Status.CANCELLED,
                recorded_by=request.user,
                redacted_payload={"reason": reason},
            )
        audit(request.user, "HOUSEHOLD_CASH_ENTITLEMENT_CANCELLED", entitlement, before=before, after={**self.get_serializer(entitlement).data, "reason": reason}, tenant=entitlement.program.tenant)
        return Response(self.get_serializer(entitlement).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def correct(self, request, pk=None):
        reason = require_reason(request.data.get("reason"))
        entitlement = CashEntitlement.objects.select_for_update().get(pk=self.get_object().pk)
        serializer = self.get_serializer(entitlement, data={"amount": request.data.get("amount")}, partial=True)
        serializer.is_valid(raise_exception=True)
        amount = serializer.validated_data["amount"]
        validate_cash_amount_or_raise(
            program=entitlement.program,
            household=entitlement.household,
            amount=amount,
            entity_type="CASH_ENTITLEMENT",
            entity_id=str(entitlement.pk),
        )
        before = self.get_serializer(entitlement).data
        self._save_with_budget_check(serializer, creating=False)
        entitlement.refresh_from_db()
        audit(
            request.user,
            "HOUSEHOLD_CASH_ENTITLEMENT_CORRECTED",
            entitlement,
            before=before,
            after={**self.get_serializer(entitlement).data, "reason": reason},
            tenant=entitlement.program.tenant,
        )
        return Response(self.get_serializer(entitlement).data)
