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
