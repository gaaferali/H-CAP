import uuid
import json
import csv
import io
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256

from django.core.serializers.json import DjangoJSONEncoder
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.core import signing
from django.utils import timezone
from openpyxl import load_workbook
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .services import AI_AUTOMATION_ENABLED, run_deduplication_check, run_automated_reconciliation, run_anomaly_detection, verified_program_summary
from .complaint import analyze_complaint
from .models import (
    AISignal,
    AuditEvent,
    AutomationExecution,
    AutomationRule,
    ActivityDependency,
    Beneficiary,
    Budget,
    Complaint,
    ComplaintAIAnalysis,
    Enrollment,
    Household,
    PaymentChannelConfig,
    PaymentEvent,
    PaymentInstruction,
    PaymentBatch,
    PDMResponse,
    Program,
    ProgramActivity,
    ReconciliationItem,
    ReviewTask,
    Tenant,
    User,
    HouseholdEligibility, HouseholdEnrollment, CashEntitlement, Warehouse, NFIItem,
    NFIEntitlement, StockMovement, DistributionEvent, DistributionAllocation, DistributionIssue,
)
from .serializers import (
    AuditEventSerializer,
    AISignalSerializer,
    AutomationExecutionSerializer,
    AutomationRuleSerializer,
    ActivityDependencySerializer,
    BeneficiarySerializer,
    BudgetSerializer,
    ComplaintSerializer,
    ComplaintAIAnalysisSerializer,
    EnrollmentSerializer,
    HouseholdSerializer,
    LoginSerializer,
    PaymentChannelConfigSerializer,
    PaymentEventSerializer,
    PaymentInstructionSerializer,
    PaymentBatchSerializer,
    ProgramSerializer,
    ProgramActivitySerializer,
    ReconciliationItemSerializer,
    ReviewTaskSerializer,
    UserSerializer,
    TenantSerializer,
    national_id_digest,
    national_id_reference,
    normalize_national_id,
    normalize_phone_number,
    HouseholdEligibilitySerializer, HouseholdEnrollmentSerializer, CashEntitlementSerializer,
    WarehouseSerializer, NFIItemSerializer, NFIEntitlementSerializer, StockMovementSerializer,
    DistributionEventSerializer, DistributionAllocationSerializer, DistributionIssueSerializer,
)


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return response
    details = response.data
    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        message = "Sign in is required or your session has expired. Please sign in again."
    elif response.status_code == status.HTTP_404_NOT_FOUND:
        message = "The requested record was not found or is not available to your account."
    elif isinstance(details, dict) and details.get("detail"):
        message = str(details["detail"])
    elif isinstance(details, dict):
        field, value = next(iter(details.items()), ("", "Please correct the highlighted fields."))
        first_value = value[0] if isinstance(value, list) and value else value
        if field == "non_field_errors":
            message = str(first_value)
        else:
            field_label = field.replace("_", " ").capitalize() if field else ""
            message = f"{field_label}: {first_value}" if field_label else str(first_value)
    elif isinstance(details, list) and details:
        message = str(details[0])
    else:
        message = "Your request could not be completed. Please try again."
    response.data = {
        "error": {
            "code": exc.__class__.__name__,
            "message": message,
            "fields": details if isinstance(details, dict) else {},
            "correlation_id": str(uuid.uuid4()),
        }
    }
    return response


def audit(user, action, entity, before=None, after=None, tenant=None):
    tenant = tenant or getattr(user, "tenant", None)
    if tenant:
        AuditEvent.objects.create(
            tenant=tenant,
            actor=user,
            action=action,
            entity_type=entity.__class__.__name__,
            entity_id=str(entity.pk),
            before=json.loads(json.dumps(before or {}, cls=DjangoJSONEncoder)),
            after=json.loads(json.dumps(after or {}, cls=DjangoJSONEncoder)),
            correlation_id=str(uuid.uuid4()),
        )


def scoped_program(request, program_id):
    programs = Program.objects.all() if request.user.is_superuser else Program.objects.filter(tenant=request.user.tenant)
    return get_object_or_404(programs, id=program_id)


def normalize_import_header(value):
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return {
        "phone": "phone_number",
        "mobile": "phone_number",
        "mobile_number": "phone_number",
        "beneficiary_no": "beneficiary_number",
        "national_id_number": "national_id",
    }.get(key, key)


def normalize_import_rows(rows):
    return [{normalize_import_header(key): value for key, value in row.items()} for row in rows]


class TenantScopedModelViewSet(viewsets.ModelViewSet):
    tenant_field = "tenant"

    def tenant_filter(self):
        return {self.tenant_field: self.request.user.tenant}

    def get_queryset(self):
        if self.request.user.is_superuser:
            return self.queryset.all()
        return self.queryset.filter(**self.tenant_filter())

    def perform_create(self, serializer):
        kwargs = {}
        if "tenant" in [field.name for field in serializer.Meta.model._meta.fields]:
            kwargs["tenant"] = self.request.user.tenant
        if "created_by" in [field.name for field in serializer.Meta.model._meta.fields]:
            kwargs["created_by"] = self.request.user
        instance = serializer.save(**kwargs)
        audit(self.request.user, f"{serializer.Meta.model.__name__.upper()}_CREATED", instance, after=serializer.data)

    def perform_update(self, serializer):
        before = self.get_serializer(serializer.instance).data
        instance = serializer.save()
        audit(self.request.user, f"{serializer.Meta.model.__name__.upper()}_UPDATED", instance, before=before, after=serializer.data)

    def destroy(self, request, *args, **kwargs):
        raise PermissionDenied("Deletion is disabled to preserve auditability; use a permitted status change instead")


class RoleProtectedTenantViewSet(TenantScopedModelViewSet):
    allowed_roles: set[str] = set()
    write_roles: set[str] | None = None

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.user.role not in self.allowed_roles:
            raise PermissionDenied("Your role does not have permission for this resource")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and self.write_roles is not None and request.user.role not in self.write_roles:
            raise PermissionDenied("Your role has read-only access to this resource")


class AdminOnlyTenantUserViewSet(TenantScopedModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if self.request.user.role not in {User.Role.ADMIN, User.Role.MANAGER} and not self.request.user.is_superuser:
            raise PermissionDenied("Only tenant administrators and managers can manage users")

    def get_queryset(self):
        if self.request.user.is_superuser:
            return User.objects.all()
        return User.objects.filter(tenant=self.request.user.tenant)

    def perform_create(self, serializer):
        if not self.request.user.is_superuser and serializer.validated_data.get("role") not in {
            User.Role.FIELD_OFFICER,
            User.Role.FINANCE,
            User.Role.REVIEWER,
            User.Role.SUPPORT,
            User.Role.AUDITOR,
        }:
            raise ValidationError({"role": "Tenant administrators may assign only tenant operational roles"})
        tenant = serializer.validated_data.get("tenant", self.request.user.tenant) if self.request.user.is_superuser else self.request.user.tenant
        user = serializer.save(tenant=tenant)
        audit(self.request.user, "USER_CREATED", user, after=UserSerializer(user).data, tenant=user.tenant)

    def perform_update(self, serializer):
        target = self.get_object()
        changes = serializer.validated_data
        if not self.request.user.is_superuser:
            if target.tenant_id != self.request.user.tenant_id or "tenant" in changes:
                raise PermissionDenied("Tenant administrators can only manage users in their own tenant")
            if "role" in changes and changes["role"] not in {
                User.Role.FIELD_OFFICER,
                User.Role.FINANCE,
                User.Role.REVIEWER,
                User.Role.SUPPORT,
                User.Role.AUDITOR,
            }:
                raise ValidationError({"role": "Tenant administrators may assign only tenant operational roles"})
            if target.pk == self.request.user.pk and ("role" in changes or "is_active" in changes):
                raise PermissionDenied("Tenant administrators cannot change their own role or access status")
        before = UserSerializer(target).data
        user = serializer.save()
        audit(self.request.user, "USER_UPDATED", user, before=before, after=UserSerializer(user).data)


class SystemTenantViewSet(viewsets.ModelViewSet):
    queryset = Tenant.objects.all()
    serializer_class = TenantSerializer

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.is_superuser:
            raise PermissionDenied("Only system administrators can manage tenants")

    def destroy(self, request, *args, **kwargs):
        raise PermissionDenied("Tenant deletion is disabled to preserve auditability")

    def perform_create(self, serializer):
        tenant = serializer.save()
        audit(self.request.user, "TENANT_CREATED", tenant, after=TenantSerializer(tenant).data, tenant=tenant)

    def perform_update(self, serializer):
        before = TenantSerializer(serializer.instance).data
        tenant = serializer.save()
        audit(self.request.user, "TENANT_UPDATED", tenant, before=before, after=TenantSerializer(tenant).data, tenant=tenant)


@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.validated_data["user"]
    refresh = RefreshToken.for_user(user)
    return Response({"user": UserSerializer(user).data, "access": str(refresh.access_token), "refresh": str(refresh)})


@api_view(["POST"])
def logout_view(request):
    refresh_token = request.data.get("refresh")
    if refresh_token:
        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError:
            pass
    return Response({"status": "signed_out"})


@api_view(["GET"])
def me_view(request):
    return Response(UserSerializer(request.user).data)


@api_view(["POST"])
def profile_details_view(request):
    current_password = str(request.data.get("current_password") or "")
    full_name = str(request.data.get("full_name") or "").strip()
    email = str(request.data.get("email") or "").strip().lower()
    if not request.user.check_password(current_password):
        raise ValidationError({"current_password": "Current password is incorrect."})
    if not full_name:
        raise ValidationError({"full_name": "Full name is required."})
    try:
        validate_email(email)
    except DjangoValidationError as exc:
        raise ValidationError({"email": "Enter a valid work email address."}) from exc
    if User.objects.exclude(pk=request.user.pk).filter(email__iexact=email).exists():
        raise ValidationError({"email": "This work email is already used by another account."})
    before = {"full_name": request.user.full_name, "email": request.user.email}
    request.user.full_name = full_name
    request.user.email = email
    request.user.save(update_fields=["full_name", "email"])
    audit(request.user, "PROFILE_DETAILS_UPDATED", request.user, before=before, after={"full_name": full_name, "email": email}, tenant=request.user.tenant)
    return Response(UserSerializer(request.user).data)


@api_view(["POST"])
def profile_password_view(request):
    current_password = str(request.data.get("current_password") or "")
    new_password = str(request.data.get("new_password") or "")
    confirm_password = str(request.data.get("confirm_password") or "")
    if not request.user.check_password(current_password):
        raise ValidationError({"current_password": "Current password is incorrect."})
    if len(new_password) < 8:
        raise ValidationError({"new_password": "New password must contain at least 8 characters."})
    if new_password != confirm_password:
        raise ValidationError({"confirm_password": "New password confirmation does not match."})
    if request.user.check_password(new_password):
        raise ValidationError({"new_password": "Choose a new password that is different from the current password."})
    request.user.set_password(new_password)
    request.user.save(update_fields=["password"])
    audit(request.user, "PROFILE_PASSWORD_UPDATED", request.user, after={"password_changed": True}, tenant=request.user.tenant)
    return Response({"status": "password_updated"})


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
        instruction_ids = list(PaymentInstruction.objects.filter(enrollment_id__in=enrollment_ids).values_list("id", flat=True))
        activity_ids = list(program.activities.values_list("id", flat=True))
        rule_ids = list(program.automation_rules.values_list("id", flat=True))
        audit(request.user, "PROGRAM_DELETED", program, after={"deleted_dependency_counts": {"households": len(household_ids), "beneficiaries": len(beneficiary_ids), "enrollments": len(enrollment_ids), "payment_instructions": len(instruction_ids), "activities": len(activity_ids), "automation_rules": len(rule_ids)}}, tenant=program.tenant)
        Complaint.objects.filter(Q(beneficiary_id__in=beneficiary_ids) | Q(instruction_id__in=instruction_ids)).delete()
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
    def channels(self, request, pk=None):
        program = self.get_object()
        if not program.cash_enabled:
            raise ValidationError("Cash assistance is disabled for this program")
        if request.method == "GET":
            return Response(PaymentChannelConfigSerializer(program.channels.all(), many=True).data)
        serializer = PaymentChannelConfigSerializer(data={**request.data, "program": str(program.id)})
        serializer.is_valid(raise_exception=True)
        channel = serializer.save()
        audit(request.user, "PAYMENT_CHANNEL_CONFIG_CREATED", channel, after=serializer.data, tenant=program.tenant)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


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


class HouseholdViewSet(RoleProtectedTenantViewSet):
    queryset = Household.objects.select_related("program")
    serializer_class = HouseholdSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.REVIEWER, User.Role.MANAGER}
    write_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.MANAGER}


class BeneficiaryViewSet(RoleProtectedTenantViewSet):
    queryset = Beneficiary.objects.select_related("household", "household__tenant")
    serializer_class = BeneficiarySerializer
    tenant_field = "household__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.REVIEWER, User.Role.SUPPORT, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.MANAGER}

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        if request.user.role not in {User.Role.ADMIN, User.Role.MANAGER}:
            raise PermissionDenied("Only administrators and managers can delete beneficiaries")
        beneficiary = self.get_object()
        enrollment_ids = list(beneficiary.enrollments.values_list("id", flat=True))
        instruction_ids = list(PaymentInstruction.objects.filter(enrollment_id__in=enrollment_ids).values_list("id", flat=True))
        audit(request.user, "BENEFICIARY_DELETED", beneficiary, after={"deleted_dependency_counts": {"enrollments": len(enrollment_ids), "payment_instructions": len(instruction_ids)}}, tenant=beneficiary.household.tenant)
        Complaint.objects.filter(Q(beneficiary=beneficiary) | Q(instruction_id__in=instruction_ids)).delete()
        PaymentEvent.objects.filter(instruction_id__in=instruction_ids).delete()
        ReconciliationItem.objects.filter(instruction_id__in=instruction_ids).delete()
        PaymentInstruction.objects.filter(id__in=instruction_ids).delete()
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


class PaymentInstructionViewSet(RoleProtectedTenantViewSet):
    queryset = PaymentInstruction.objects.select_related("enrollment", "beneficiary", "channel_config", "enrollment__program")
    serializer_class = PaymentInstructionSerializer
    tenant_field = "enrollment__program__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}

    def get_queryset(self):
        return super().get_queryset().filter(enrollment__program__cash_enabled=True)

    def update(self, request, *args, **kwargs):
        raise PermissionDenied("Payment instructions are immutable; use the controlled simulator actions")

    def partial_update(self, request, *args, **kwargs):
        raise PermissionDenied("Payment instructions are immutable; use the controlled simulator actions")

    def perform_create(self, serializer):
        instruction = serializer.save(
            created_by=self.request.user,
            status=PaymentInstruction.Status.CREATED,
            idempotency_key=str(uuid.uuid4()),
        )
        PaymentEvent.objects.create(
            instruction=instruction,
            event_type=PaymentEvent.EventType.CREATED,
            to_status=PaymentInstruction.Status.CREATED,
            recorded_by=self.request.user,
            redacted_payload={"simulation": True},
        )
        audit(
            self.request.user,
            "PAYMENT_INSTRUCTION_CREATED",
            instruction,
            after=serializer.data,
            tenant=instruction.enrollment.program.tenant,
        )

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def simulate(self, request, pk=None):
        instruction = self.get_object()
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
        if new_status == PaymentInstruction.Status.REVERSED and old_status not in {PaymentInstruction.Status.SUCCESS, PaymentInstruction.Status.FAILED}:
            raise ValidationError({"outcome": "Only success or failed instructions can be reversed"})
        instruction.status = new_status
        instruction.provider_reference = instruction.provider_reference or f"SIM-{str(instruction.id)[:8].upper()}"
        instruction.save(update_fields=["status", "provider_reference"])
        event = PaymentEvent.objects.create(
            instruction=instruction,
            event_type=event_type,
            provider_status=PaymentEvent.ProviderStatus.ACCEPTED if outcome in {"submit", "retry"} else PaymentEvent.ProviderStatus.SETTLED if outcome == "success" else PaymentEvent.ProviderStatus.FAILED,
            provider_transaction_id=request.data.get("provider_transaction_id", ""),
            from_status=old_status,
            to_status=new_status,
            recorded_by=request.user,
            redacted_payload={"simulation": True, "no_external_provider_call": True},
        )
        audit(
            request.user,
            "PAYMENT_SIMULATED",
            instruction,
            before={"status": old_status},
            after={"status": new_status},
            tenant=instruction.enrollment.program.tenant,
        )
        return Response(PaymentEventSerializer(event).data)

    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        return Response(PaymentEventSerializer(self.get_object().events.all().order_by("created_at"), many=True).data)


class ComplaintViewSet(RoleProtectedTenantViewSet):
    queryset = Complaint.objects.select_related("beneficiary", "beneficiary__household")
    serializer_class = ComplaintSerializer
    tenant_field = "beneficiary__household__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.SUPPORT, User.Role.MANAGER}


COMPLAINT_AI_READ_ROLES = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER, User.Role.AUDITOR}
COMPLAINT_AI_WRITE_ROLES = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER}


def complaint_ai_scope(request, complaint_id):
    if request.user.role not in COMPLAINT_AI_READ_ROLES:
        raise PermissionDenied("Your role does not have permission for complaint AI analysis")
    complaints = Complaint.objects.select_related("beneficiary__household__program", "instruction")
    if not request.user.is_superuser:
        complaints = complaints.filter(beneficiary__household__tenant=request.user.tenant)
    return get_object_or_404(complaints, id=complaint_id)


@api_view(["POST"])
def complaint_ai_analyze_view(request, complaint_id):
    complaint = complaint_ai_scope(request, complaint_id)
    if request.user.role not in COMPLAINT_AI_WRITE_ROLES:
        raise PermissionDenied("Your role does not have permission to run complaint AI analysis")
    analysis = analyze_complaint(complaint, actor=request.user)
    audit(request.user, "COMPLAINT_AI_ANALYZED", analysis, after=ComplaintAIAnalysisSerializer(analysis).data, tenant=analysis.tenant)
    return Response(ComplaintAIAnalysisSerializer(analysis).data)


@api_view(["GET"])
def complaint_ai_analysis_view(request, complaint_id):
    complaint = complaint_ai_scope(request, complaint_id)
    analysis = get_object_or_404(ComplaintAIAnalysis, complaint=complaint)
    return Response(ComplaintAIAnalysisSerializer(analysis).data)


@api_view(["POST"])
def complaint_ai_review_view(request, complaint_id):
    complaint = complaint_ai_scope(request, complaint_id)
    if request.user.role not in COMPLAINT_AI_WRITE_ROLES:
        raise PermissionDenied("Your role does not have permission to review complaint AI analysis")
    decision = request.data.get("decision")
    if decision not in ComplaintAIAnalysis.ReviewDecision.values:
        raise ValidationError({"decision": "Use ACCEPT, MODIFY, or DISMISS."})
    analysis = get_object_or_404(ComplaintAIAnalysis, complaint=complaint)
    before = ComplaintAIAnalysisSerializer(analysis).data
    analysis.review_decision = decision
    analysis.reviewer_note = request.data.get("reviewer_note", "")
    analysis.reviewed_by = request.user
    analysis.reviewed_at = timezone.now()
    analysis.save(update_fields=["review_decision", "reviewer_note", "reviewed_by", "reviewed_at", "updated_at"])
    audit(request.user, "COMPLAINT_AI_REVIEWED", analysis, before=before, after=ComplaintAIAnalysisSerializer(analysis).data, tenant=analysis.tenant)
    return Response(ComplaintAIAnalysisSerializer(analysis).data)


@api_view(["POST"])
def complaint_ai_analyze_bulk_view(request):
    if request.user.role not in COMPLAINT_AI_WRITE_ROLES:
        raise PermissionDenied("Your role does not have permission to run complaint AI analysis")
    complaints = Complaint.objects.select_related("beneficiary__household__program", "instruction").filter(status=Complaint.Status.OPEN)
    if not request.user.is_superuser:
        complaints = complaints.filter(beneficiary__household__tenant=request.user.tenant)
    analyses = [analyze_complaint(complaint, actor=request.user) for complaint in complaints]
    audit(request.user, "COMPLAINT_AI_BULK_ANALYZED", request.user, after={"analyzed_count": len(analyses)}, tenant=request.user.tenant)
    return Response({"analyzed_count": len(analyses), "analyses": ComplaintAIAnalysisSerializer(analyses, many=True).data})


class BudgetViewSet(RoleProtectedTenantViewSet):
    queryset = Budget.objects.select_related("program")
    serializer_class = BudgetSerializer
    tenant_field = "program__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}


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


class AISignalViewSet(RoleProtectedTenantViewSet):
    queryset = AISignal.objects.select_related("program")
    serializer_class = AISignalSerializer
    tenant_field = "tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER}

    def create(self, request, *args, **kwargs):
        raise PermissionDenied("AI signals are created only by controlled detection services")

    def update(self, request, *args, **kwargs):
        raise PermissionDenied("Use the controlled AI signal review action")

    def partial_update(self, request, *args, **kwargs):
        raise PermissionDenied("Use the controlled AI signal review action")

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        signal = self.get_object()
        signal.status = request.data.get("status", AISignal.Status.REVIEWED)
        signal.review_note = request.data.get("review_note", "")
        signal.reviewed_by = request.user
        signal.reviewed_at = timezone.now()
        signal.save(update_fields=["status", "review_note", "reviewed_by", "reviewed_at"])
        audit(request.user, "AI_SIGNAL_REVIEWED", signal, after={"status": signal.status}, tenant=signal.tenant)
        return Response(AISignalSerializer(signal).data)


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


class ReviewTaskViewSet(RoleProtectedTenantViewSet):
    queryset = ReviewTask.objects.select_related("program", "assigned_to")
    serializer_class = ReviewTaskSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER, User.Role.SUPPORT}

    def create(self, request, *args, **kwargs):
        raise PermissionDenied("Review tasks are created by controlled workflows and automation rules")

    def update(self, request, *args, **kwargs):
        raise PermissionDenied("Use the controlled review-task resolution action")

    def partial_update(self, request, *args, **kwargs):
        raise PermissionDenied("Use the controlled review-task resolution action")

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        task = self.get_object()
        task.status = ReviewTask.Status.RESOLVED
        task.resolution = request.data.get("resolution", "")
        task.resolved_at = timezone.now()
        task.save(update_fields=["status", "resolution", "resolved_at"])
        audit(request.user, "REVIEW_TASK_RESOLVED", task, after={"status": task.status}, tenant=task.tenant)
        return Response(ReviewTaskSerializer(task).data)


class AutomationRuleViewSet(TenantScopedModelViewSet):
    queryset = AutomationRule.objects.select_related("program")
    serializer_class = AutomationRuleSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER}

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.user.role not in self.allowed_roles:
            raise PermissionDenied("Your role does not have permission for automation rules")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.user.role not in self.write_roles:
            raise PermissionDenied("Your role has read-only access to automation rules")


class AutomationExecutionViewSet(TenantScopedModelViewSet):
    queryset = AutomationExecution.objects.select_related("rule")
    serializer_class = AutomationExecutionSerializer

    def get_queryset(self):
        if self.request.user.role not in {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER, User.Role.AUDITOR}:
            raise PermissionDenied("Your role does not have permission to inspect automation executions")
        return super().get_queryset()

    def create(self, request, *args, **kwargs):
        raise PermissionDenied("Automation executions are created by the controlled automation endpoint")

    def update(self, request, *args, **kwargs):
        raise PermissionDenied("Automation executions are immutable")

    def partial_update(self, request, *args, **kwargs):
        raise PermissionDenied("Automation executions are immutable")


class AuditEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditEventSerializer

    def get_queryset(self):
        if self.request.user.is_superuser:
            return AuditEvent.objects.all()
        if self.request.user.role not in {User.Role.ADMIN, User.Role.MANAGER, User.Role.AUDITOR}:
            raise PermissionDenied("Audit history is restricted to tenant administrators, managers, and auditors")
        return AuditEvent.objects.filter(tenant=self.request.user.tenant)


@api_view(["GET"])
def reports_view(request):
    requested_tenant = request.query_params.get("tenant")
    if request.user.is_superuser:
        tenant = get_object_or_404(Tenant, id=requested_tenant) if requested_tenant else None
        programs = Program.objects.filter(tenant=tenant) if tenant else Program.objects.all()
    else:
        if requested_tenant and requested_tenant != str(request.user.tenant_id):
            raise PermissionDenied("You cannot access another tenant's dashboard")
        tenant = request.user.tenant
        programs = Program.objects.filter(tenant=tenant)
    requested_program = request.query_params.get("program")
    selected_program = None
    if requested_program:
        selected_program = get_object_or_404(programs, id=requested_program)
        programs = programs.filter(id=selected_program.id)
    households = Household.objects.filter(program__in=programs)
    payments = PaymentInstruction.objects.filter(enrollment__program__in=programs)
    enrollments = Enrollment.objects.filter(program__in=programs)
    complaints = Complaint.objects.filter(beneficiary__household__program__in=programs)
    beneficiaries = Beneficiary.objects.filter(household__program__in=programs)
    household_eligibility = HouseholdEligibility.objects.filter(program__in=programs)
    household_enrollment = HouseholdEnrollment.objects.filter(program__in=programs)
    nfi_entitlements = NFIEntitlement.objects.filter(program__in=programs)
    nfi_issues = DistributionIssue.objects.filter(entitlement__in=nfi_entitlements)
    budgets = Budget.objects.filter(program__in=programs)
    distributed = payments.filter(status=PaymentInstruction.Status.SUCCESS).aggregate(total=Sum("amount"))["total"] or 0
    approved = enrollments.filter(status=Enrollment.Status.APPROVED).count()
    approved_amount = sum((program.transfer_amount * program.enrollments.filter(status=Enrollment.Status.APPROVED).count() for program in programs), 0)
    budget_totals = budgets.aggregate(planned=Sum("planned_total"), actual=Sum("actual_total"))
    dashboards = {
        "beneficiaries": beneficiaries.count(),
        "approvals": approved,
        "paid": payments.filter(status=PaymentInstruction.Status.SUCCESS).count(),
        "pending": payments.filter(status__in=[PaymentInstruction.Status.DRAFT, PaymentInstruction.Status.CREATED, PaymentInstruction.Status.SUBMITTED]).count(),
        "failed": payments.filter(status=PaymentInstruction.Status.FAILED).count(),
        "amounts_approved": str(approved_amount),
        "amounts_distributed": str(distributed),
        "budget_planned": str(budget_totals["planned"] or 0),
        "budget_actual": str(budget_totals["actual"] or 0),
        "budget_remaining": str((budget_totals["planned"] or 0) - (budget_totals["actual"] or 0)),
        "geography": list(households.values("location").annotate(count=Count("id")).order_by("location")),
        "reconciliation": list(payments.values("status").annotate(count=Count("id"), amount=Sum("amount")).order_by("status")),
        "complaints": list(complaints.values("status").annotate(count=Count("id")).order_by("status")),
        "assigned_complaints": list(complaints.filter(assigned_to=request.user).values("id", "category", "severity", "status", "resolution_notes", "beneficiary__full_name", "beneficiary__household__program__name").order_by("status", "-created_at")),
        "operational_exceptions": payments.filter(Q(status=PaymentInstruction.Status.FAILED) | Q(complaints__isnull=False)).distinct().count(),
        "eligible_households": household_eligibility.filter(status=HouseholdEligibility.Status.ELIGIBLE).values("household_id").distinct().count(),
        "enrolled_households": household_enrollment.filter(status=HouseholdEnrollment.Status.ACCEPTED).values("household_id").distinct().count(),
        "payment_beneficiaries": enrollments.filter(status=Enrollment.Status.APPROVED, assistance_modality__in=[Enrollment.AssistanceModality.CASH, Enrollment.AssistanceModality.CASH_NFI]).values("beneficiary_id").distinct().count(),
        "nfi_beneficiaries": enrollments.filter(status=Enrollment.Status.APPROVED, assistance_modality__in=[Enrollment.AssistanceModality.NFI, Enrollment.AssistanceModality.CASH_NFI]).values("beneficiary_id").distinct().count(),
        "nfi_entitlement_quantity": nfi_entitlements.aggregate(total=Sum("quantity"))["total"] or 0,
        "nfi_distributed_quantity": nfi_issues.aggregate(total=Sum("actual_quantity"))["total"] or 0,
        "nfi_pending_delivery": nfi_entitlements.filter(issues__isnull=True).count(),
        "available_nfi_stock": NFIItem.objects.filter(program__in=programs).aggregate(total=Sum("available_quantity"))["total"] or 0,
        "program_kpis": list(programs.values("id", "name", "country", "currency", "status", "tenant__name").annotate(enrollments=Count("enrollments"))),
    }
    role_dashboard_fields = {
        User.Role.FIELD_OFFICER: {"beneficiaries", "approvals"},
        User.Role.SUPPORT: {"beneficiaries", "complaints", "assigned_complaints", "operational_exceptions"},
        User.Role.REVIEWER: {"beneficiaries", "approvals", "failed", "operational_exceptions"},
        User.Role.FINANCE: {
            "beneficiaries", "approvals", "paid", "pending", "failed", "amounts_approved",
            "amounts_distributed", "budget_planned", "budget_actual", "budget_remaining",
            "reconciliation", "operational_exceptions", "program_kpis",
        },
    }
    if request.user.is_superuser or request.user.role in {User.Role.ADMIN, User.Role.MANAGER, User.Role.AUDITOR}:
        permitted_dashboards = dashboards
    else:
        permitted_fields = role_dashboard_fields.get(request.user.role, set())
        permitted_dashboards = {field: dashboards[field] for field in permitted_fields}
    if request.user.is_superuser:
        permitted_dashboards["tenant_count"] = Tenant.objects.count() if not tenant else 1
        permitted_dashboards["system_administrator"] = True
    return Response({
        "scope": {
            "tenant_id": str(tenant.id) if tenant else None,
            "tenant_name": tenant.name if tenant else "All tenants",
            "program_id": str(selected_program.id) if selected_program else None,
            "program_name": selected_program.name if selected_program else "All programs",
        },
        "dashboards": permitted_dashboards,
    })


@api_view(["GET"])
def program_summary_view(request, program_id):
    program = scoped_program(request, program_id)
    payments = PaymentInstruction.objects.filter(enrollment__program=program)
    enrollments = Enrollment.objects.filter(program=program)
    complaints = Complaint.objects.filter(beneficiary__household__program=program)
    reconciliation = ReconciliationItem.objects.filter(program=program)
    budget = getattr(program, "budget", None)
    return Response({
        "program": {"id": str(program.id), "name": program.name, "tenant_id": str(program.tenant_id), "tenant_name": program.tenant.name, "country_code": program.country_code, "currency": program.currency, "reporting_currency": program.reporting_currency, "timezone": program.timezone, "language": program.language},
        "beneficiaries": Beneficiary.objects.filter(household__program=program).count(),
        "eligibility": list(enrollments.values("eligibility_status").annotate(count=Count("id")).order_by("eligibility_status")),
        "approvals": enrollments.filter(status=Enrollment.Status.APPROVED).count(),
        "payments": list(payments.values("status").annotate(count=Count("id"), amount=Sum("amount")).order_by("status")),
        "amounts": {"approved": str(program.transfer_amount * enrollments.filter(status=Enrollment.Status.APPROVED).count()), "distributed": str(payments.filter(status=PaymentInstruction.Status.SUCCESS).aggregate(total=Sum("amount"))["total"] or 0)},
        "failures": payments.filter(status=PaymentInstruction.Status.FAILED).count(),
        "reconciliation": list(reconciliation.values("issue_type", "status").annotate(count=Count("id")).order_by("issue_type")),
        "pdm": {"summary_source": "derived_api"},
        "complaints": list(complaints.values("status", "severity").annotate(count=Count("id")).order_by("status")),
        "budget": {"planned_total": str(budget.planned_total), "actual_total": str(budget.actual_total), "remaining": str(budget.planned_total - budget.actual_total), "status": budget.status} if budget else None,
        "verified": True,
    })


@api_view(["POST"])
def imports_view(request):
    if request.user.role not in {User.Role.FIELD_OFFICER, User.Role.MANAGER} and not request.user.is_superuser:
        raise PermissionDenied("Only field officers and tenant managers can import mapped records")
    upload = request.FILES.get("file")
    program_id = request.data.get("program_id")
    confirmation_token = request.data.get("confirmation_token")
    if not upload or not program_id:
        raise ValidationError({"file": "A CSV or XLSX file is required", "program_id": "Required"})
    if upload.size > 10 * 1024 * 1024:
        raise ValidationError({"file": "Maximum file size is 10 MB"})
    program = scoped_program(request, program_id)
    tenant = program.tenant
    content = upload.read()
    content_hash = sha256(content).hexdigest()
    if upload.name.lower().endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    elif upload.name.lower().endswith(".xlsx"):
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheet = workbook.active
        values = list(sheet.values)
        headers = [str(value).strip() if value is not None else "" for value in values[:1][0]] if values else []
        rows = [dict(zip(headers, row)) for row in values[1:] if any(value is not None and str(value).strip() for value in row)]
    else:
        raise ValidationError({"file": "Use a .csv or .xlsx file"})
    rows = normalize_import_rows(rows)
    available_columns = set(rows[0].keys()) if rows else set()
    required_columns = {"client_generated_id", "household_size", "location", "full_name"}
    missing_columns = sorted(required_columns - available_columns)
    if not ({"national_id", "beneficiary_number"} & available_columns):
        missing_columns.append("national_id (or beneficiary_number)")
    if missing_columns:
        raise ValidationError({"file": f"Missing required columns: {', '.join(missing_columns)}"})
    errors, normalized_rows = [], []
    for row_number, row in enumerate(rows, start=2):
        client_id = str(row.get("client_generated_id") or "").strip()
        raw_national_id = str(row.get("national_id") or row.get("beneficiary_number") or "").strip()
        full_name = str(row.get("full_name") or "").strip()
        location = str(row.get("location") or "").strip()
        try:
            household_size = int(row.get("household_size"))
            if household_size < 1:
                raise ValueError
        except (TypeError, ValueError):
            errors.append({"row": row_number, "field": "household_size", "message": "Use a positive whole number"})
            continue
        if not client_id or not raw_national_id or not full_name or not location:
            errors.append({"row": row_number, "field": "required", "message": "client_generated_id, location, national_id (or beneficiary_number), and full_name are required"})
            continue
        try:
            normalized_national_id = normalize_national_id(raw_national_id)
            phone_number = normalize_phone_number(row.get("phone_number") or row.get("phone_last4"))
        except ValidationError as exc:
            errors.append({"row": row_number, "field": "beneficiary", "message": str(exc.detail[0])})
            continue
        verification_status = str(row.get("verification_status") or Beneficiary.VerificationStatus.PENDING).upper()
        if verification_status not in Beneficiary.VerificationStatus.values:
            errors.append({"row": row_number, "field": "verification_status", "message": "Use PENDING, VERIFIED, or REJECTED"})
            continue
        normalized_rows.append({"client_id": client_id, "size": household_size, "location": location, "registration_date": str(row.get("registration_date") or timezone.localdate()), "number": national_id_reference(normalized_national_id), "full_name": full_name, "gender": str(row.get("gender") or "").strip(), "phone_number": phone_number, "phone_last4": phone_number[-4:] if phone_number else "", "national_id_hash": str(row.get("national_id_hash") or national_id_digest(normalized_national_id)).strip(), "consent_given": str(row.get("consent_given") or "").lower() in {"1", "true", "yes", "y"}, "verification_status": verification_status})
    duplicate_candidates = 0
    for row in normalized_rows:
        matches = Beneficiary.objects.filter(household__tenant=tenant, household__program=program)
        if row["national_id_hash"]:
            matches = matches.filter(national_id_hash=row["national_id_hash"])
        elif row["phone_last4"]:
            matches = matches.filter(phone_last4=row["phone_last4"])
        else:
            continue
        duplicate_candidates += matches.count()
    if not confirmation_token:
        token = signing.dumps({"user_id": str(request.user.id), "tenant_id": str(tenant.id), "program_id": str(program.id), "content_hash": content_hash})
        return Response({
            "status": "ready_for_confirmation" if normalized_rows else "invalid",
            "rows_received": len(rows),
            "rows_valid": len(normalized_rows),
            "rows_invalid": len(errors),
            "errors": errors,
            "preview_rows": normalized_rows[:25],
            "possible_duplicate_matches": duplicate_candidates,
            "confirmation_token": token if normalized_rows else None,
            "client_identifiers_preserved": True,
        })
    try:
        confirmation = signing.loads(confirmation_token, max_age=15 * 60)
    except signing.BadSignature as exc:
        raise ValidationError({"confirmation_token": "Preview confirmation has expired or is invalid. Preview the file again."}) from exc
    if confirmation != {"user_id": str(request.user.id), "tenant_id": str(tenant.id), "program_id": str(program.id), "content_hash": content_hash}:
        raise ValidationError({"confirmation_token": "This confirmation does not match the selected program and file."})
    if not normalized_rows:
        raise ValidationError({"file": "There are no valid rows to import."})
    households_created = beneficiaries_created = duplicate_flags = 0
    with transaction.atomic():
        for row in normalized_rows:
            household, created = Household.objects.get_or_create(tenant=tenant, client_generated_id=row["client_id"], defaults={"program": program, "household_size": row["size"], "location": row["location"], "registration_date": row["registration_date"], "created_by": request.user})
            if household.program_id != program.id:
                raise ValidationError({"client_generated_id": f"{row['client_id']} belongs to another program"})
            beneficiary, beneficiary_created = Beneficiary.objects.update_or_create(household=household, number=row["number"], defaults={"full_name": row["full_name"], "gender": row["gender"], "phone_number": row["phone_number"], "phone_last4": row["phone_last4"], "national_id_hash": row["national_id_hash"], "consent_given": row["consent_given"], "verification_status": row["verification_status"], "created_by": request.user})
            households_created += int(created)
            beneficiaries_created += int(beneficiary_created)
            if AI_AUTOMATION_ENABLED:
                duplicate_flags += int(run_deduplication_check(beneficiary)["matches"] > 0)
    audit(request.user, "IMPORT_COMPLETED", program, after={"rows": len(normalized_rows), "rows_invalid": len(errors), "households_created": households_created, "beneficiaries_created": beneficiaries_created}, tenant=tenant)
    return Response({
        "status": "completed",
        "rows_received": len(rows),
        "rows_invalid": len(errors),
        "errors": errors,
        "households_created": households_created,
        "beneficiaries_created": beneficiaries_created,
        "duplicate_flags": duplicate_flags,
        "client_identifiers_preserved": True,
    }, status=status.HTTP_201_CREATED)


def pdm_program_metrics(program, responses=None):
    successful_payments = PaymentInstruction.objects.filter(
        enrollment__program=program,
        status=PaymentInstruction.Status.SUCCESS,
    )
    paid_beneficiaries = successful_payments.values("beneficiary_id").distinct().count()
    distributed_amount = successful_payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    recorded_recipients = (responses or PDMResponse.objects.filter(program=program)).aggregate(total=Sum("received_count"))["total"] or 0
    complaints = Complaint.objects.filter(beneficiary__household__program=program).count()
    return {
        "paid_beneficiaries": paid_beneficiaries,
        "distributed_amount": distributed_amount,
        "recorded_recipients": recorded_recipients,
        "remaining_recipients": max(paid_beneficiaries - recorded_recipients, 0),
        "complaints": complaints,
        "complaint_rate": (Decimal(complaints) * Decimal("100") / Decimal(paid_beneficiaries)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if paid_beneficiaries else Decimal("0"),
    }


@api_view(["GET", "POST"])
def pdm_view(request):
    permitted_roles = {User.Role.ADMIN, User.Role.SUPPORT, User.Role.FIELD_OFFICER, User.Role.MANAGER, User.Role.AUDITOR}
    if request.user.role not in permitted_roles:
        raise PermissionDenied("Your role does not have permission for PDM")
    if request.method == "GET":
        responses = PDMResponse.objects.all() if request.user.is_superuser else PDMResponse.objects.filter(tenant=request.user.tenant)
        requested_tenant = request.query_params.get("tenant")
        if requested_tenant:
            if not request.user.is_superuser and requested_tenant != str(request.user.tenant_id):
                raise PermissionDenied("You cannot access another tenant's PDM data")
            responses = responses.filter(tenant=requested_tenant)
        if request.query_params.get("program"):
            program = scoped_program(request, request.query_params["program"])
            if requested_tenant and str(program.tenant_id) != str(requested_tenant):
                raise PermissionDenied("The selected program does not belong to the selected tenant")
            responses = responses.filter(program=program)
        return Response(list(responses.values("id", "tenant", "tenant__name", "program", "program__name", "channel", "location", "received_count", "received_rate", "amount_received", "access_problem_rate", "complaint_rate", "satisfaction", "created_at")))

    if request.user.role in {User.Role.AUDITOR, User.Role.SUPPORT}:
        raise PermissionDenied("Your PDM access is limited to summaries")
    if request.data.get("program") in {None, ""} or request.data.get("received_count") in {None, ""}:
        raise ValidationError({"program": "Required", "received_count": "Enter the number of people who received assistance."})
    try:
        received_count = int(request.data["received_count"])
    except (TypeError, ValueError):
        raise ValidationError({"received_count": "Enter a whole number."})
    if received_count < 1:
        raise ValidationError({"received_count": "Enter at least one recipient."})
    program = scoped_program(request, request.data["program"])
    existing_responses = PDMResponse.objects.filter(program=program)
    metrics = pdm_program_metrics(program, existing_responses)
    if not metrics["paid_beneficiaries"]:
        raise ValidationError({"received_count": "No successfully simulated payments are available for this program yet."})
    if received_count > metrics["remaining_recipients"]:
        raise ValidationError({"received_count": f"Only {metrics['remaining_recipients']} recipients remain to be recorded for this program."})
    try:
        access_problem_rate = Decimal(str(request.data.get("access_problem_rate") or 0))
        satisfaction = Decimal(str(request.data.get("satisfaction") or 0))
    except Exception:
        raise ValidationError("Access problem rate and satisfaction must be numbers.")
    if any(value < 0 or value > 100 for value in (access_problem_rate, satisfaction)):
        raise ValidationError("Access problem rate and satisfaction must be between 0 and 100.")
    channel = PaymentChannelConfig.objects.filter(program=program, is_active=True).order_by("provider_name").values_list("channel_type", flat=True).first() or "UNSPECIFIED"
    location = Household.objects.filter(program=program).order_by("registration_date").values_list("location", flat=True).first() or "UNSPECIFIED"
    received_rate = (Decimal(received_count) * Decimal("100") / Decimal(metrics["paid_beneficiaries"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amount_received = (metrics["distributed_amount"] * Decimal(received_count) / Decimal(metrics["paid_beneficiaries"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    response = PDMResponse.objects.create(tenant=program.tenant, program=program, channel=channel, location=location, received_count=received_count, received_rate=received_rate, amount_received=amount_received, access_problem_rate=access_problem_rate, complaint_rate=metrics["complaint_rate"], satisfaction=satisfaction, created_by=request.user)
    audit(request.user, "PDM_RESPONSE_CREATED", response, after={"program": str(program.id), "received_count": received_count}, tenant=program.tenant)
    return Response({"status": "created", "id": str(response.id), "submitted_at": response.created_at}, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def pdm_summary_view(request):
    if request.user.role not in {User.Role.ADMIN, User.Role.SUPPORT, User.Role.FIELD_OFFICER, User.Role.MANAGER, User.Role.AUDITOR}:
        raise PermissionDenied("Your role does not have permission for PDM summaries")
    responses = PDMResponse.objects.all() if request.user.is_superuser else PDMResponse.objects.filter(tenant=request.user.tenant)
    selected_tenant = None
    requested_tenant = request.query_params.get("tenant")
    if request.user.is_superuser and requested_tenant:
        selected_tenant = get_object_or_404(Tenant, id=requested_tenant)
        responses = responses.filter(tenant=selected_tenant)
    elif not request.user.is_superuser:
        if requested_tenant and requested_tenant != str(request.user.tenant_id):
            raise PermissionDenied("You cannot access another tenant's PDM data")
        selected_tenant = request.user.tenant
    selected_program = None
    for field in ("program", "channel", "location"):
        if request.query_params.get(field):
            if field == "program":
                selected_program = scoped_program(request, request.query_params[field])
                if selected_tenant and selected_program.tenant_id != selected_tenant.id:
                    raise PermissionDenied("The selected program does not belong to the selected tenant")
                responses = responses.filter(program=selected_program)
            else:
                responses = responses.filter(**{field: request.query_params[field]})
    summary_programs = Program.objects.all() if request.user.is_superuser else Program.objects.filter(tenant=request.user.tenant)
    if selected_tenant:
        summary_programs = summary_programs.filter(tenant=selected_tenant)
    if selected_program:
        summary_programs = summary_programs.filter(id=selected_program.id)
    successful_payments = PaymentInstruction.objects.filter(
        enrollment__program__in=summary_programs,
        status=PaymentInstruction.Status.SUCCESS,
    )
    paid_beneficiaries = successful_payments.values("beneficiary_id").distinct().count()
    distributed_amount = successful_payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    complaints = Complaint.objects.filter(beneficiary__household__program__in=summary_programs).count()
    totals = responses.aggregate(received_count=Sum("received_count"), amount_received=Sum("amount_received"), access_problem_rate=Sum("access_problem_rate"), satisfaction=Sum("satisfaction"), count=Count("id"))
    count = totals["count"] or 0
    recorded_recipients = totals["received_count"] or 0
    channels = list(responses.exclude(channel="").values_list("channel", flat=True).distinct().order_by("channel"))
    locations = list(responses.exclude(location="").values_list("location", flat=True).distinct().order_by("location"))
    eligible_households = HouseholdEligibility.objects.filter(program__in=summary_programs, status=HouseholdEligibility.Status.ELIGIBLE).values("household_id").distinct().count()
    enrolled_households = HouseholdEnrollment.objects.filter(program__in=summary_programs, status=HouseholdEnrollment.Status.ACCEPTED).values("household_id").distinct().count()
    approved = Enrollment.objects.filter(program__in=summary_programs, status=Enrollment.Status.APPROVED)
    cash_beneficiaries = approved.filter(assistance_modality__in=[Enrollment.AssistanceModality.CASH, Enrollment.AssistanceModality.CASH_NFI]).values("beneficiary_id").distinct().count()
    nfi_beneficiaries = approved.filter(assistance_modality__in=[Enrollment.AssistanceModality.NFI, Enrollment.AssistanceModality.CASH_NFI]).values("beneficiary_id").distinct().count()
    cash_nfi_beneficiaries = approved.filter(assistance_modality=Enrollment.AssistanceModality.CASH_NFI).values("beneficiary_id").distinct().count()
    nfi_entitlements = NFIEntitlement.objects.filter(program__in=summary_programs)
    nfi_issues = DistributionIssue.objects.filter(entitlement__in=nfi_entitlements)
    nfi_stock = NFIItem.objects.filter(program__in=summary_programs).aggregate(total=Sum("available_quantity"))["total"] or 0
    nfi_allocated = StockMovement.objects.filter(program__in=summary_programs, movement_type=StockMovement.MovementType.ALLOCATION).aggregate(total=Sum("quantity"))["total"] or 0
    warehouse_stock = list(NFIItem.objects.filter(program__in=summary_programs).values("warehouse_id", "warehouse__name", "warehouse__location").annotate(initial_quantity=Sum("initial_quantity"), available_quantity=Sum("available_quantity")).order_by("warehouse__name"))
    delivery_exceptions = nfi_issues.exclude(delivery_status=DistributionIssue.DeliveryStatus.RECEIVED).count()
    return Response({
        "program": {"id": str(selected_program.id), "name": selected_program.name} if selected_program else None,
        "tenant": {"id": str(selected_tenant.id), "name": selected_tenant.name} if selected_tenant else None,
        "channels": channels,
        "locations": locations,
        "paid_beneficiaries": paid_beneficiaries,
        "recorded_recipients": recorded_recipients,
        "remaining_recipients": max(paid_beneficiaries - recorded_recipients, 0),
        "distributed_amount": str(distributed_amount),
        "complaints": complaints,
        "received_rate": float(Decimal(recorded_recipients) * Decimal("100") / Decimal(paid_beneficiaries)) if paid_beneficiaries else 0,
        "amount_received": str(totals["amount_received"] or 0),
        "access_problem_rate": float(totals["access_problem_rate"] / count) if count else 0,
        "complaint_rate": float(Decimal(complaints) * Decimal("100") / Decimal(paid_beneficiaries)) if paid_beneficiaries else 0,
        "satisfaction": float(totals["satisfaction"] / count) if count else 0,
        "responses": count,
        "eligible_households": eligible_households,
        "enrolled_households": enrolled_households,
        "approved_beneficiaries": approved.count(),
        "cash_beneficiaries": cash_beneficiaries,
        "nfi_beneficiaries": nfi_beneficiaries,
        "cash_nfi_beneficiaries": cash_nfi_beneficiaries,
        "cash_entitlements": CashEntitlement.objects.filter(program__in=summary_programs).count(),
        "nfi_entitlements": nfi_entitlements.count(),
        "nfi_entitled_quantity": nfi_entitlements.aggregate(total=Sum("quantity"))["total"] or 0,
        "nfi_allocated_quantity": nfi_allocated,
        "nfi_distributed_quantity": nfi_issues.aggregate(total=Sum("actual_quantity"))["total"] or 0,
        "nfi_pending_delivery": nfi_entitlements.filter(issues__isnull=True).count(),
        "distribution_events": DistributionEvent.objects.filter(program__in=summary_programs).count(),
        "available_nfi_stock": nfi_stock,
        "nfi_enabled": summary_programs.filter(nfi_enabled=True).exists(),
        "warehouse_stock": warehouse_stock,
        "open_delivery_exceptions": delivery_exceptions,
        "delivery_status_counts": {status: nfi_issues.filter(delivery_status=status).count() for status, _label in DistributionIssue.DeliveryStatus.choices},
    })


@api_view(["POST"])
def registration_sync_view(request):
    if request.user.role not in {User.Role.ADMIN, User.Role.FIELD_OFFICER}:
        raise PermissionDenied("Only field officers and administrators can synchronize registrations")
    program_id = request.data.get("program_id")
    household_data = request.data.get("household", request.data)
    client_generated_id = household_data.get("client_generated_id")
    if not program_id or not client_generated_id:
        raise ValidationError({"program_id": "Required", "client_generated_id": "Required"})

    program = get_object_or_404(Program, id=program_id, tenant=request.user.tenant)
    household, household_created = Household.objects.get_or_create(
        tenant=program.tenant,
        client_generated_id=client_generated_id,
        defaults={
            "program": program,
            "household_size": household_data.get("household_size", 1),
            "location": household_data.get("location", "Unspecified"),
            "registration_date": household_data.get("registration_date") or timezone.localdate(),
            "created_by": request.user,
        },
    )
    if household.program_id != program.id:
        raise ValidationError({"client_generated_id": "Already belongs to another program"})

    beneficiaries_created = 0
    for beneficiary_data in request.data.get("beneficiaries", []):
        raw_national_id = beneficiary_data.get("national_id") or beneficiary_data.get("number")
        full_name = beneficiary_data.get("full_name")
        if not raw_national_id or not full_name:
            raise ValidationError({"beneficiaries": "Each beneficiary requires national_id and full_name"})
        normalized_national_id = normalize_national_id(raw_national_id)
        phone_number = normalize_phone_number(beneficiary_data.get("phone_number") or beneficiary_data.get("phone_last4"))
        _, created = Beneficiary.objects.get_or_create(
            household=household,
            number=national_id_reference(normalized_national_id),
            defaults={
                "full_name": full_name,
                "gender": beneficiary_data.get("gender", ""),
                "phone_number": phone_number,
                "phone_last4": phone_number[-4:] if phone_number else "",
                "national_id_hash": national_id_digest(normalized_national_id),
                "consent_given": beneficiary_data.get("consent_given", False),
                "created_by": request.user,
            },
        )
        beneficiaries_created += int(created)
    audit(request.user, "OFFLINE_REGISTRATION_SYNCED", household, after={"client_generated_id": client_generated_id}, tenant=program.tenant)
    return Response({
        "status": "synchronized",
        "household_id": str(household.id),
        "household_created": household_created,
        "beneficiaries_created": beneficiaries_created,
        "client_generated_id": client_generated_id,
        "tenant_isolated": True,
    }, status=status.HTTP_201_CREATED if household_created else status.HTTP_200_OK)


@api_view(['POST'])
def trigger_deduplication_api(request, beneficiary_id=None):
    """
    نقطة نهاية لتشغيل فحص التكرار لمستفيد معين أو عام
    """
    if not AI_AUTOMATION_ENABLED:
        return Response({"status": "disabled", "message": "AI and automation are disabled."})
    if request.user.role not in {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER}:
        raise PermissionDenied("Only administrators, managers, and reviewers can run deduplication")
    if not beneficiary_id:
        raise ValidationError({"beneficiary_id": "Select a beneficiary before running duplicate detection."})
    try:
        beneficiary_uuid = uuid.UUID(str(beneficiary_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError({"beneficiary_id": "Select a valid beneficiary record."}) from exc
    beneficiaries = Beneficiary.objects.all() if request.user.is_superuser else Beneficiary.objects.filter(household__tenant=request.user.tenant)
    beneficiary = get_object_or_404(beneficiaries, id=beneficiary_uuid)
    result = run_deduplication_check(beneficiary)
    return Response({
        "status": "completed",
        "beneficiary_id": str(beneficiary.pk),
        "duplicate_flagged": result["matches"] > 0,
        "matches": result["matches"],
        "signals": AISignalSerializer(result["signals"], many=True).data,
        "advisory_only": True,
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
def trigger_reconciliation_api(request):
    """
    نقطة نهاية لتشغيل التسوية التلقائية لدفعة مالية بناءً على تقرير المزود
    """
    if not AI_AUTOMATION_ENABLED:
        return Response({"status": "disabled", "message": "AI and automation are disabled."})
    if request.user.role not in {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}:
        raise PermissionDenied("Your role does not have permission to reconcile payment batches")
    batch_id = request.data.get("batch_id")
    provider_report_data = request.data.get("provider_report_data", {})
    if not isinstance(provider_report_data, dict):
        raise ValidationError({"provider_report_data": "Provide a JSON object keyed by provider reference."})
    for provider_reference, record in provider_report_data.items():
        if not isinstance(record, dict):
            raise ValidationError({"provider_report_data": f"The record for {provider_reference} must be a JSON object."})
        if "amount" not in record or "status" not in record:
            raise ValidationError({"provider_report_data": f"The record for {provider_reference} must include amount and status."})
        try:
            if Decimal(str(record["amount"])) < 0:
                raise ValueError
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise ValidationError({"provider_report_data": f"The amount for {provider_reference} must be a non-negative number."}) from exc
        if str(record["status"]).upper() not in {"SUCCESS", "FAILED", "REVERSED", "PENDING"}:
            raise ValidationError({"provider_report_data": f"The status for {provider_reference} must be SUCCESS, FAILED, REVERSED, or PENDING."})
    
    batches = PaymentBatch.objects.all() if request.user.is_superuser else PaymentBatch.objects.filter(tenant=request.user.tenant)
    batch = get_object_or_404(batches, id=batch_id)
    results = run_automated_reconciliation(batch, provider_report_data)
    return Response({
        "status": "success",
        "batch_id": batch_id,
        "reconciliation_summary": results
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
def trigger_anomaly_detection_api(request):
    """
    نقطة نهاية لكشف الانحرافات والأنماط الشاذة للدفعة المالية أو الاحتيال
    """
    if not AI_AUTOMATION_ENABLED:
        return Response({"status": "disabled", "message": "AI and automation are disabled."})
    if request.user.role not in {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}:
        raise PermissionDenied("Your role does not have permission to run payment risk scans")
    batch_id = request.data.get("batch_id")
    batches = PaymentBatch.objects.all() if request.user.is_superuser else PaymentBatch.objects.filter(tenant=request.user.tenant)
    batch = get_object_or_404(batches, id=batch_id)
    signals = run_anomaly_detection(batch)
    return Response({
        "status": "completed",
        "batch_id": batch_id,
        "anomalies_detected": len(signals),
        "signals": AISignalSerializer(signals, many=True).data,
        "advisory_only": True,
    }, status=status.HTTP_200_OK)


@api_view(["GET", "POST"])
def ai_automation_status_view(request):
    if not AI_AUTOMATION_ENABLED:
        return Response({"status": "disabled", "message": "AI and automation are disabled.", "rules": [], "signals_count": 0, "review_tasks_open": 0})
    read_roles = {
        User.Role.ADMIN,
        User.Role.MANAGER,
        User.Role.REVIEWER,
        User.Role.FINANCE,
        User.Role.AUDITOR,
    }
    write_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER}
    allowed_roles = read_roles if request.method == "GET" else write_roles
    if request.user.role not in allowed_roles:
        raise PermissionDenied("Your role does not have permission for AI and automation")
    scope = {} if request.user.is_superuser else {"tenant": request.user.tenant}
    if request.method == "GET":
        rules = AutomationRule.objects.filter(**scope)
        signals = AISignal.objects.filter(**scope)
        return Response({
            "status": "active",
            "message": "Advisory signals and controlled automation are available.",
            "rules": list(rules.values("id", "event_name", "action_name", "is_active", "priority")),
            "signals_count": signals.count(),
            "review_tasks_open": ReviewTask.objects.filter(**scope, status__in=[ReviewTask.Status.OPEN, ReviewTask.Status.ASSIGNED]).count(),
            "advisory_only": True,
        })
    action_name = request.data.get("action")
    if action_name not in {"CREATE_REVIEW_TASK", "RECONCILE_BATCH", "RUN_DEDUPLICATION", "RUN_RISK_SCAN"}:
        raise ValidationError({"action": "Use CREATE_REVIEW_TASK, RECONCILE_BATCH, RUN_DEDUPLICATION, or RUN_RISK_SCAN"})
    return Response({"status": "accepted", "action": action_name, "execution": "controlled", "advisory_only": True})


def automation_execution_response(execution):
    data = AutomationExecutionSerializer(execution).data
    planned_action = execution.planned_action or {}
    review_task_id = planned_action.get("review_task_id")
    if execution.status == AutomationExecution.Status.COMPLETED:
        data.update({
            "outcome": "HUMAN_REVIEW_TASK_CREATED",
            "message": "A human review task was added to the review queue. No payment or eligibility decision was made.",
            "review_task_created": bool(review_task_id),
        })
    elif execution.status == AutomationExecution.Status.DRY_RUN:
        data.update({
            "outcome": "DRY_RUN_NOT_EXECUTED",
            "message": "No automatic payment, eligibility, fraud, or escalation action was executed. A human must make the decision.",
            "review_task_created": False,
        })
    else:
        data.update({
            "outcome": execution.status,
            "message": "The controlled automation request was recorded for review.",
            "review_task_created": bool(review_task_id),
        })
    return data


@api_view(["POST"])
def automation_execute_view(request):
    if not AI_AUTOMATION_ENABLED:
        return Response({"status": "disabled", "message": "AI and automation are disabled."})
    if request.user.role not in {User.Role.ADMIN, User.Role.MANAGER}:
        raise PermissionDenied("Only administrators and managers can execute configured automation")
    rules = AutomationRule.objects.all() if request.user.is_superuser else AutomationRule.objects.filter(tenant=request.user.tenant)
    rule = get_object_or_404(rules, id=request.data.get("rule_id"))
    if not rule.is_active:
        raise ValidationError({"rule_id": "The automation rule is not active"})
    idempotency_key = request.data.get("idempotency_key")
    if not idempotency_key:
        raise ValidationError({"idempotency_key": "Required to prevent duplicate execution"})
    event_payload = request.data.get("event_payload", {})
    if not isinstance(event_payload, dict):
        raise ValidationError({"event_payload": "Provide event details as a JSON object."})
    if rule.action_name == "CREATE_REVIEW_TASK":
        task_type = event_payload.get("task_type", ReviewTask.TaskType.DATA_QUALITY)
        priority = event_payload.get("priority", ReviewTask.Priority.MEDIUM)
        if task_type not in ReviewTask.TaskType.values:
            raise ValidationError({"event_payload": {"task_type": "Choose a valid human review type."}})
        if priority not in ReviewTask.Priority.values:
            raise ValidationError({"event_payload": {"priority": "Choose a valid priority."}})
    with transaction.atomic():
        execution, created = AutomationExecution.objects.get_or_create(
            tenant=rule.tenant,
            rule=rule,
            idempotency_key=idempotency_key,
            defaults={"event_payload": event_payload, "planned_action": {"action": rule.action_name}, "status": AutomationExecution.Status.BLOCKED},
        )
        if not created:
            return Response(automation_execution_response(execution))
        if rule.action_name == "CREATE_REVIEW_TASK":
            _review_task = ReviewTask.objects.create(
                tenant=rule.tenant,
                program=rule.program,
                task_type=task_type,
                entity_type=event_payload.get("entity_type", "AUTOMATION_EVENT"),
                entity_id=str(event_payload.get("entity_id", execution.id)),
                priority=priority,
                resolution="Created by controlled automation; human resolution required.",
            )
            execution.status = AutomationExecution.Status.COMPLETED
            execution.planned_action = {"action": rule.action_name, "review_task_id": str(_review_task.id)}
        else:
            execution.status = AutomationExecution.Status.DRY_RUN
            execution.planned_action = {"action": rule.action_name, "reason": "Decision-sensitive actions remain human controlled."}
        execution.save(update_fields=["status", "planned_action"])
        audit(request.user, "AUTOMATION_EXECUTED", execution, after=AutomationExecutionSerializer(execution).data, tenant=rule.tenant)
    return Response(automation_execution_response(execution), status=status.HTTP_201_CREATED)


@api_view(["POST"])
def ai_copilot_view(request):
    if not AI_AUTOMATION_ENABLED:
        return Response({"status": "disabled", "message": "AI copilot is disabled."})
    if request.user.role not in {User.Role.ADMIN, User.Role.MANAGER, User.Role.AUDITOR, User.Role.REVIEWER}:
        raise PermissionDenied("Your role does not have permission for verified reporting")
    program_id = request.data.get("program_id") or request.query_params.get("program_id")
    question = request.data.get("question", "")
    if not program_id:
        raise ValidationError({"program_id": "Required"})
    program = scoped_program(request, program_id)
    return Response({"answer": "Verified metrics are returned for human interpretation; no eligibility, fraud, or payment decision is made automatically.", "question": question, "metrics": verified_program_summary(program), "verified": True, "advisory_only": True})


@api_view(["GET"])
def pipeline_status_view(request):
    if not AI_AUTOMATION_ENABLED:
        return Response({"status": "disabled", "message": "AI and automation are disabled.", "pipeline": [], "automation_execution": "disabled"})
    if request.user.role not in {User.Role.ADMIN, User.Role.MANAGER, User.Role.AUDITOR}:
        raise PermissionDenied("Your role does not have permission to inspect pipeline status")
    signal_scope = {} if request.user.is_superuser else {"tenant": request.user.tenant}
    beneficiary_scope = {} if request.user.is_superuser else {"household__tenant": request.user.tenant}
    return Response({"status": "completed", "pipeline": ["validation", "deduplication", "risk_signals", "human_review", "reconciliation", "verified_reporting"], "records": {"beneficiaries": Beneficiary.objects.filter(**beneficiary_scope).count(), "signals": AISignal.objects.filter(**signal_scope).count(), "review_tasks": ReviewTask.objects.filter(**signal_scope).count()}, "automation_execution": "controlled", "tenant_isolated": not request.user.is_superuser})


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
    allowed_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER}
    tenant_field = "household__tenant"

    def perform_create(self, serializer):
        household = serializer.validated_data["household"]
        program = serializer.validated_data.get("program") or household.program
        if not HouseholdEligibility.objects.filter(household=household, program=program, status=HouseholdEligibility.Status.ELIGIBLE).exists():
            raise ValidationError("A household must be eligible before enrollment")
        instance = serializer.save(program=program, decided_by=self.request.user, decided_at=timezone.now())
        if instance.status == HouseholdEnrollment.Status.ACCEPTED:
            Enrollment.objects.filter(beneficiary__household=household, program=program).update(status=Enrollment.Status.APPROVED, approved_by=self.request.user, approved_at=timezone.now())
        audit(self.request.user, "HOUSEHOLD_ENROLLMENT_DECISION", instance, after=serializer.data)


class CashEntitlementViewSet(RoleProtectedTenantViewSet):
    queryset = CashEntitlement.objects.select_related("beneficiary", "program")
    serializer_class = CashEntitlementSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}
    tenant_field = "program__tenant"

    def get_queryset(self):
        return super().get_queryset().filter(program__cash_enabled=True)

    def perform_create(self, serializer):
        beneficiary, program = serializer.validated_data["beneficiary"], serializer.validated_data["program"]
        if not program.cash_enabled:
            raise ValidationError("Cash assistance is disabled for this program")
        if not Enrollment.objects.filter(beneficiary=beneficiary, program=program, status=Enrollment.Status.APPROVED).exists():
            raise ValidationError("Beneficiary is not approved for assistance")
        serializer.save(created_by=self.request.user)


class WarehouseViewSet(RoleProtectedTenantViewSet):
    queryset = Warehouse.objects.select_related("program")
    serializer_class = WarehouseSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.FIELD_OFFICER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER}
    tenant_field = "program__tenant"

    def get_queryset(self):
        return super().get_queryset().filter(program__nfi_enabled=True)

    def perform_create(self, serializer):
        program = serializer.validated_data["program"]
        if not program.nfi_enabled:
            raise ValidationError("NFI is disabled for this program")
        serializer.save(created_by=self.request.user)


class NFIItemViewSet(RoleProtectedTenantViewSet):
    queryset = NFIItem.objects.select_related("program", "warehouse")
    serializer_class = NFIItemSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.FIELD_OFFICER, User.Role.FINANCE, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER}
    tenant_field = "program__tenant"

    def get_queryset(self):
        return super().get_queryset().filter(program__nfi_enabled=True)

    def perform_create(self, serializer):
        data = serializer.validated_data
        program, warehouse = data["program"], data["warehouse"]
        if not program.nfi_enabled:
            raise ValidationError("NFI is disabled for this program")
        if warehouse.program_id != program.id:
            raise ValidationError("Warehouse must belong to the selected program")
        quantity = data.get("initial_quantity", data.get("available_quantity", 0))
        instance = serializer.save(initial_quantity=quantity, available_quantity=quantity, created_by=self.request.user)
        if quantity:
            StockMovement.objects.create(program=program, warehouse=warehouse, item=instance, quantity=quantity, movement_type=StockMovement.MovementType.ENTRY, reference="initial stock", created_by=self.request.user)


class NFIEntitlementViewSet(RoleProtectedTenantViewSet):
    queryset = NFIEntitlement.objects.select_related("beneficiary", "program", "item")
    serializer_class = NFIEntitlementSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.FIELD_OFFICER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.FIELD_OFFICER}
    tenant_field = "program__tenant"

    def get_queryset(self):
        return super().get_queryset().filter(program__nfi_enabled=True)

    def perform_create(self, serializer):
        data = serializer.validated_data
        beneficiary, program, item = data["beneficiary"], data["program"], data["item"]
        if not program.nfi_enabled:
            raise ValidationError("NFI assistance is disabled for this program")
        warehouse = data.get("warehouse")
        if not warehouse:
            raise ValidationError("Warehouse is required for NFI entitlement")
        if item.program_id != program.id or warehouse.program_id != program.id or item.warehouse_id != warehouse.id or not item.active:
            raise ValidationError("Item is not active for this program")
        if not Enrollment.objects.filter(beneficiary=beneficiary, program=program, status=Enrollment.Status.APPROVED, assistance_modality__in=[Enrollment.AssistanceModality.NFI, Enrollment.AssistanceModality.CASH_NFI]).exists():
            raise ValidationError("Beneficiary is not approved for NFI assistance")
        household = beneficiary.household
        if household.eligibility_decisions.exists() and not household.eligibility_decisions.filter(program=program, status=HouseholdEligibility.Status.ELIGIBLE).exists():
            raise ValidationError("Household is not eligible for NFI assistance")
        if household.household_enrollments.exists() and not household.household_enrollments.filter(program=program, status=HouseholdEnrollment.Status.ACCEPTED).exists():
            raise ValidationError("Household enrollment has not been accepted")
        with transaction.atomic():
            locked = NFIItem.objects.select_for_update().get(pk=item.pk)
            if data["quantity"] > locked.available_quantity:
                raise ValidationError("Insufficient stock")
            locked.available_quantity -= data["quantity"]
            locked.save(update_fields=["available_quantity"])
            instance = serializer.save(created_by=self.request.user)
            StockMovement.objects.create(program=program, warehouse=locked.warehouse, item=locked, quantity=data["quantity"], movement_type=StockMovement.MovementType.ALLOCATION, reference=str(instance.pk), created_by=self.request.user)
            audit(self.request.user, "NFI_STOCK_ALLOCATED", instance, after={"warehouse": str(locked.warehouse_id), "item": str(locked.id), "quantity": data["quantity"]}, tenant=program.tenant)


class StockMovementViewSet(RoleProtectedTenantViewSet):
    queryset = StockMovement.objects.select_related("program", "warehouse", "item")
    serializer_class = StockMovementSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.FIELD_OFFICER, User.Role.FINANCE, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER}
    tenant_field = "program__tenant"


class DistributionEventViewSet(RoleProtectedTenantViewSet):
    queryset = DistributionEvent.objects.select_related("program")
    serializer_class = DistributionEventSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.FIELD_OFFICER, User.Role.FINANCE, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.FIELD_OFFICER}
    tenant_field = "program__tenant"

    def get_queryset(self):
        return super().get_queryset().filter(program__nfi_enabled=True)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entitlement_ids = request.data.get("entitlement_ids") or []
        if not isinstance(entitlement_ids, list) or not entitlement_ids:
            raise ValidationError({"entitlement_ids": "Select one or more existing NFI entitlements"})
        program = serializer.validated_data["program"]
        warehouse = serializer.validated_data["warehouse"]
        entitlements = list(NFIEntitlement.objects.select_for_update().select_related("beneficiary__household", "item", "warehouse").filter(id__in=entitlement_ids, program=program, warehouse=warehouse, status=NFIEntitlement.Status.ACTIVE))
        if len(entitlements) != len(set(str(item) for item in entitlement_ids)):
            raise ValidationError({"entitlement_ids": "Each entitlement must be active and belong to the selected program and warehouse"})
        for entitlement in entitlements:
            if not entitlement.item.active or entitlement.item.warehouse_id != warehouse.id:
                raise ValidationError("Entitlement item is not active in the selected warehouse")
            if not Enrollment.objects.filter(beneficiary=entitlement.beneficiary, program=program, status=Enrollment.Status.APPROVED, assistance_modality__in=[Enrollment.AssistanceModality.NFI, Enrollment.AssistanceModality.CASH_NFI]).exists():
                raise ValidationError("Every selected beneficiary must be approved for NFI assistance")
            allocated = DistributionAllocation.objects.filter(entitlement=entitlement).aggregate(total=Sum("allocated_quantity"))["total"] or 0
            if allocated >= entitlement.quantity:
                raise ValidationError("An entitlement is already fully assigned to a distribution event")
        event = serializer.save(created_by=request.user)
        allocations = []
        for entitlement in entitlements:
            allocated = DistributionAllocation.objects.filter(entitlement=entitlement).aggregate(total=Sum("allocated_quantity"))["total"] or 0
            remaining = entitlement.quantity - allocated
            allocations.append(DistributionAllocation.objects.create(beneficiary=entitlement.beneficiary, entitlement=entitlement, event=event, item=entitlement.item, planned_quantity=remaining, allocated_quantity=remaining, status="ALLOCATED", created_by=request.user))
        audit(request.user, "DISTRIBUTION_EVENT_CREATED", event, after={"entitlement_ids": [str(item.id) for item in entitlements], "allocation_ids": [str(item.id) for item in allocations]}, tenant=program.tenant)
        headers = self.get_success_headers(serializer.data)
        return Response(self.get_serializer(event).data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        program = serializer.validated_data["program"]
        warehouse = serializer.validated_data.get("warehouse")
        if not program.nfi_enabled:
            raise ValidationError("NFI assistance is disabled for this program")
        if not warehouse:
            raise ValidationError({"warehouse": "A warehouse is required for a distribution event"})
        if warehouse.program_id != program.id:
            raise ValidationError("Warehouse must belong to the selected program")
        serializer.save(created_by=self.request.user)


class DistributionAllocationViewSet(RoleProtectedTenantViewSet):
    queryset = DistributionAllocation.objects.select_related("beneficiary", "entitlement", "event", "item")
    serializer_class = DistributionAllocationSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.FIELD_OFFICER, User.Role.FINANCE, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.FIELD_OFFICER}
    tenant_field = "event__program__tenant"

    def perform_create(self, serializer):
        data = serializer.validated_data
        entitlement, event, item, beneficiary = data["entitlement"], data["event"], data["item"], data["beneficiary"]
        if entitlement.beneficiary_id != beneficiary.id or entitlement.item_id != item.id or event.program_id != entitlement.program_id:
            raise ValidationError("Allocation must match the entitlement beneficiary, item, and program")
        if not event.warehouse_id or entitlement.warehouse_id != event.warehouse_id or item.warehouse_id != event.warehouse_id:
            raise ValidationError("Allocation warehouse must match the entitlement and event warehouse")
        planned = data["planned_quantity"]
        allocated = data.get("allocated_quantity", 0)
        if planned > entitlement.quantity or allocated > planned:
            raise ValidationError("Allocation cannot exceed the entitlement quantity")
        serializer.save(created_by=self.request.user)


class DistributionIssueViewSet(RoleProtectedTenantViewSet):
    queryset = DistributionIssue.objects.select_related("beneficiary", "entitlement", "event", "item")
    serializer_class = DistributionIssueSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER, User.Role.FIELD_OFFICER, User.Role.FINANCE, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER}
    tenant_field = "event__program__tenant"

    def perform_create(self, serializer):
        data = serializer.validated_data
        entitlement, event = data["entitlement"], data["event"]
        if event.program_id != entitlement.program_id or data["item"].id != entitlement.item_id:
            raise ValidationError("Distribution issue relationships must belong to the same entitlement and program")
        if data["actual_quantity"] > data["planned_quantity"] or data["actual_quantity"] > entitlement.quantity:
            raise ValidationError("Delivered quantity cannot exceed planned entitlement quantity")
        instance = serializer.save(created_by=self.request.user)
        movement_type = StockMovement.MovementType.DISTRIBUTION if instance.actual_quantity else StockMovement.MovementType.ISSUE
        if instance.actual_quantity:
            StockMovement.objects.create(program=event.program, warehouse=event.warehouse, item=instance.item, quantity=instance.actual_quantity, movement_type=movement_type, reference=str(instance.pk), created_by=self.request.user)
        if instance.actual_quantity >= instance.entitlement.quantity:
            instance.entitlement.status = NFIEntitlement.Status.DISTRIBUTED
            instance.entitlement.save(update_fields=["status", "updated_at"])
        audit(self.request.user, "DISTRIBUTION_DELIVERY_RECORDED", instance, after=serializer.data)

    @transaction.atomic
    def perform_update(self, serializer):
        before = DistributionIssueSerializer(serializer.instance).data
        old_actual = serializer.instance.actual_quantity
        instance = serializer.save()
        delta = instance.actual_quantity - old_actual
        if delta > 0:
            StockMovement.objects.create(program=instance.event.program, warehouse=instance.event.warehouse, item=instance.item, quantity=delta, movement_type=StockMovement.MovementType.DISTRIBUTION, reference=str(instance.pk), created_by=self.request.user)
        if instance.actual_quantity >= instance.entitlement.quantity and instance.entitlement.status != NFIEntitlement.Status.DISTRIBUTED:
            instance.entitlement.status = NFIEntitlement.Status.DISTRIBUTED
            instance.entitlement.save(update_fields=["status", "updated_at"])
        audit(self.request.user, "DISTRIBUTION_DELIVERY_UPDATED", instance, before=before, after=DistributionIssueSerializer(instance).data)
