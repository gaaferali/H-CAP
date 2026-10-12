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



class HouseholdViewSet(RoleProtectedTenantViewSet):
    queryset = Household.objects.select_related("program")
    serializer_class = HouseholdSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.REVIEWER, User.Role.MANAGER, User.Role.FINANCE}
    write_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.MANAGER}

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


class BeneficiaryViewSet(RoleProtectedTenantViewSet):
    queryset = Beneficiary.objects.select_related("household", "household__tenant")
    serializer_class = BeneficiarySerializer
    tenant_field = "household__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.REVIEWER, User.Role.SUPPORT, User.Role.MANAGER, User.Role.AUDITOR, User.Role.FINANCE}
    write_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.MANAGER}

    @transaction.atomic
    def perform_create(self, serializer):
        household = Household.objects.select_for_update().get(
            pk=serializer.validated_data["household"].pk
        )
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
        if request.user.role not in {User.Role.ADMIN, User.Role.MANAGER}:
            raise PermissionDenied("Only administrators and managers can delete beneficiaries")
        beneficiary = self.get_object()
        enrollment_ids = list(beneficiary.enrollments.values_list("id", flat=True))
        instruction_ids = list(
            PaymentInstruction.objects.filter(
                Q(enrollment_id__in=enrollment_ids)
                | Q(beneficiary=beneficiary)
                | Q(cash_entitlement__beneficiary=beneficiary)
            ).values_list("id", flat=True)
        )
        audit(request.user, "BENEFICIARY_DELETED", beneficiary, after={"deleted_dependency_counts": {"enrollments": len(enrollment_ids), "payment_instructions": len(instruction_ids)}}, tenant=beneficiary.household.tenant)
        Complaint.objects.filter(Q(beneficiary=beneficiary) | Q(instruction_id__in=instruction_ids)).delete()
        PaymentDeliveryReview.objects.filter(payment_instruction_id__in=instruction_ids).delete()
        PaymentEvent.objects.filter(instruction_id__in=instruction_ids).delete()
        ReconciliationItem.objects.filter(instruction_id__in=instruction_ids).delete()
        PaymentInstruction.objects.filter(id__in=instruction_ids).delete()
        CashEntitlement.objects.filter(beneficiary=beneficiary).delete()
        Enrollment.objects.filter(id__in=enrollment_ids).delete()
        AISignal.objects.filter(entity_type="BENEFICIARY", entity_id=str(beneficiary.id)).delete()
        ReviewTask.objects.filter(entity_type="BENEFICIARY", entity_id=str(beneficiary.id)).delete()
        household = beneficiary.household
        beneficiary.delete()
        if not household.beneficiaries.exists():
            audit(request.user, "HOUSEHOLD_DELETED_AFTER_LAST_BENEFICIARY", household, after={"reason": "last_beneficiary_deleted"}, tenant=household.tenant)
            household.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EnrollmentViewSet(RoleProtectedTenantViewSet):
    queryset = Enrollment.objects.select_related("program", "beneficiary", "beneficiary__household")
    serializer_class = EnrollmentSerializer
    tenant_field = "program__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER}


class HouseholdEligibilityViewSet(RoleProtectedTenantViewSet):
    queryset = HouseholdEligibility.objects.select_related("household", "program")
    serializer_class = HouseholdEligibilitySerializer
    allowed_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER}
    tenant_field = "household__tenant"

    def perform_create(self, serializer):
        household = serializer.validated_data["household"]
        instance = serializer.save(program=household.program, decided_by=self.request.user, decided_at=timezone.now())
        audit(self.request.user, "HOUSEHOLD_ELIGIBILITY_DECISION", instance, after=serializer.data)


class HouseholdEnrollmentViewSet(RoleProtectedTenantViewSet):
    queryset = HouseholdEnrollment.objects.select_related("household", "program")
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

    def perform_create(self, serializer):
        household = serializer.validated_data["household"]
        program = serializer.validated_data.get("program") or household.program
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


class CashEntitlementViewSet(RoleProtectedTenantViewSet):
    queryset = CashEntitlement.objects.select_related("household", "beneficiary", "program")
    serializer_class = CashEntitlementSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR, User.Role.REVIEWER}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}
    tenant_field = "program__tenant"

    def get_queryset(self):
        return super().get_queryset().filter(program__cash_enabled=True)

    def perform_create(self, serializer):
        self._save_with_budget_check(serializer, creating=True)

    def perform_update(self, serializer):
        self._save_with_budget_check(serializer, creating=False)

    @transaction.atomic
    def _save_with_budget_check(self, serializer, creating):
        program = serializer.validated_data.get("program", serializer.instance.program if serializer.instance else None)
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
        successful_payment = PaymentInstruction.objects.select_for_update().filter(
            cash_entitlement=serializer.instance,
            status=PaymentInstruction.Status.SUCCESS,
        ).first() if not creating else None
        requested_status = serializer.validated_data.get(
            "status",
            serializer.instance.status if serializer.instance else CashEntitlement.Status.ACTIVE,
        )
        if successful_payment and (
            requested != serializer.instance.amount or requested_status == CashEntitlement.Status.CANCELLED
        ):
            raise ValidationError({"amount": "A successfully paid entitlement amount and reservation cannot be changed or cancelled"})
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
