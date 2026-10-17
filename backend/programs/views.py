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
from programs.services import (
    payment_budget_totals,
    program_dependency_summary,
    require_reason,
    scoped_program,
    transition_program,
)
from reporting.audit import audit, json_safe
from reporting.import_utils import normalize_import_header, normalize_import_rows
from reporting.models import AuditEvent, PDMResponse
from reporting.serializers import AuditEventSerializer



# Lists programs and handles setup, channels, and lifecycle actions.
class ProgramViewSet(RoleProtectedTenantViewSet):
    queryset = Program.objects.order_by("-created_at", "id")
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
        program = Program.objects.select_for_update().get(pk=self.get_object().pk)
        reason = require_reason(request.data.get("reason") or request.query_params.get("reason"))
        if program.status != Program.Status.DRAFT:
            raise ValidationError({"status": "Only an unused draft program can be deleted. Close programs with history instead."})
        dependencies = program_dependency_summary(program)
        if dependencies:
            raise ValidationError(
                {"program": "This program has related records and cannot be deleted. Keep it for audit history or close it.", "dependencies": dependencies}
            )
        audit(request.user, "PROGRAM_DELETED", program, after={"reason": reason}, tenant=program.tenant)
        program.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _transition(self, request, target_status):
        program = self.get_object()
        before = ProgramSerializer(program, context={"request": request}).data
        program, changed = transition_program(
            program=program,
            target_status=target_status,
            actor=request.user,
            reason=request.data.get("reason"),
        )
        payload = ProgramSerializer(program, context={"request": request}).data
        if changed:
            audit(
                request.user,
                f"PROGRAM_{target_status}",
                program,
                before=before,
                after={**payload, "reason": program.status_reason},
                tenant=program.tenant,
            )
        return Response(payload)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        return self._transition(request, Program.Status.ACTIVE)

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        return self._transition(request, Program.Status.PAUSED)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        return self._transition(request, Program.Status.CLOSED)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        return self._transition(request, Program.Status.PAUSED)

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


# Creates and updates the operational activities for a program.
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


# Manages the required order between program activities.
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


# Returns the budget that belongs to a cash-enabled program.
class BudgetViewSet(RoleProtectedTenantViewSet):
    queryset = Budget.objects.select_related("program")
    serializer_class = BudgetSerializer
    tenant_field = "program__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}
