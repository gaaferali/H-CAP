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
    results = run_automated_reconciliation(batch.id, provider_report_data)
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
    anomalies_detected = run_anomaly_detection(batch)
    signals = AISignal.objects.filter(
        tenant=batch.tenant,
        program=batch.program,
        entity_type="PAYMENT_INSTRUCTION",
        entity_id__in=[str(value) for value in batch.instructions.values_list("id", flat=True)],
        signal_type="PAYMENT_ANOMALY",
    ).order_by("created_at")
    return Response({
        "status": "completed",
        "batch_id": batch_id,
        "anomalies_detected": anomalies_detected,
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
    event_payload = json_safe(request.data.get("event_payload", {}))
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
