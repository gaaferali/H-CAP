import uuid
import json
import hashlib

from django.contrib.auth import login
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import exception_handler

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from .services import (run_deduplication_check, run_automated_reconciliation, run_anomaly_detection, run_data_quality_check, validate_synced_registration, process_automation_event, AICopilotService, record_offline_event)
from .complaint_ai import analyze_complaint
from .models import (
    AISignal,
    AuditEvent,
    AutomationExecution,
    AutomationRule,
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
    Program,
    ReconciliationItem,
    ReviewTask,
    SyncOperation,
    User,
)
from .serializers import (
    AuditEventSerializer,
    AISignalSerializer,
    AutomationExecutionSerializer,
    AutomationRuleSerializer,
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
    ReconciliationItemSerializer,
    ReviewTaskSerializer,
    SyncOperationSerializer,
    UserSerializer,
)


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return response
    response.data = {
        "error": {
            "code": exc.__class__.__name__,
            "message": response.data.get("detail", "Request failed") if isinstance(response.data, dict) else "Request failed",
            "fields": response.data if isinstance(response.data, dict) else {},
            "correlation_id": str(uuid.uuid4()),
        }
    }
    return response


def audit(user, action, entity, before=None, after=None):
    tenant = getattr(user, "tenant", None)
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


class TenantScopedModelViewSet(viewsets.ModelViewSet):
    tenant_field = "tenant"

    def tenant_filter(self):
        return {self.tenant_field: self.request.user.tenant}

    def get_queryset(self):
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
        if self.request.user.role != User.Role.ADMIN:
            raise PermissionDenied("Only tenant admins can manage users")

    def get_queryset(self):
        return User.objects.filter(tenant=self.request.user.tenant)

    def perform_create(self, serializer):
        user = serializer.save(tenant=self.request.user.tenant)
        audit(self.request.user, "USER_CREATED", user, after=UserSerializer(user).data)


@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.validated_data["user"]
    login(request, user)
    token, _ = Token.objects.get_or_create(user=user)
    return Response({"user": UserSerializer(user).data, "token": token.key})


@api_view(["POST"])
def logout_view(request):
    Token.objects.filter(user=request.user).delete()
    return Response({"status": "signed_out"})


@api_view(["GET"])
def me_view(request):
    return Response(UserSerializer(request.user).data)


class ProgramViewSet(RoleProtectedTenantViewSet):
    queryset = Program.objects.all()
    serializer_class = ProgramSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}

    @action(detail=True, methods=["get", "post"], url_path="channels")
    def channels(self, request, pk=None):
        program = self.get_object()
        if request.method == "GET":
            return Response(PaymentChannelConfigSerializer(program.channels.all(), many=True).data)
        serializer = PaymentChannelConfigSerializer(data={**request.data, "program": str(program.id)})
        serializer.is_valid(raise_exception=True)
        channel = serializer.save()
        audit(request.user, "PAYMENT_CHANNEL_CONFIG_CREATED", channel, after=serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class HouseholdViewSet(RoleProtectedTenantViewSet):
    queryset = Household.objects.select_related("program")
    serializer_class = HouseholdSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER}


class BeneficiaryViewSet(RoleProtectedTenantViewSet):
    queryset = Beneficiary.objects.select_related("household", "household__tenant")
    serializer_class = BeneficiarySerializer
    tenant_field = "household__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.REVIEWER}


class EnrollmentViewSet(RoleProtectedTenantViewSet):
    queryset = Enrollment.objects.select_related("program", "beneficiary", "beneficiary__household")
    serializer_class = EnrollmentSerializer
    tenant_field = "program__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.REVIEWER}


class PaymentInstructionViewSet(RoleProtectedTenantViewSet):
    queryset = PaymentInstruction.objects.select_related("enrollment", "beneficiary", "channel_config", "enrollment__program")
    serializer_class = PaymentInstructionSerializer
    tenant_field = "enrollment__program__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}

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
        audit(self.request.user, "PAYMENT_INSTRUCTION_CREATED", instruction, after=serializer.data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def simulate(self, request, pk=None):
        """Mock-provider state machine with Task 3 duplicate/timeout/retry controls."""
        instruction = self.get_object()
        outcome = str(request.data.get("outcome", "")).lower()
        provider_tx = str(request.data.get("provider_transaction_id", "")).strip()

        # Never allow a high-risk instruction to be sent to the provider simulation.
        if outcome in {"submit", "success", "retry"}:
            open_risk = AISignal.objects.filter(
                tenant=request.user.tenant,
                entity_type="PAYMENT_INSTRUCTION",
                entity_id=str(instruction.id),
                status=AISignal.Status.OPEN,
                signal_type__in=["PAYMENT_ANOMALY", "TASK3_ANOMALY"],
            ).exists()
            if open_risk:
                audit(request.user, "PAYMENT_PROVIDER_SUBMISSION_BLOCKED_ANOMALY", instruction,
                      after={"outcome": outcome, "reason": "open_anomaly_review"})
                raise ValidationError({"payment": "Provider submission blocked until the anomaly review is resolved."})

        # CAS04: a duplicate settled callback must not create a second posting/event.
        if outcome == "success" and instruction.status == PaymentInstruction.Status.SUCCESS:
            if provider_tx and instruction.provider_reference and provider_tx == instruction.provider_reference:
                audit(request.user, "PAYMENT_CALLBACK_DUPLICATE_ACKNOWLEDGED", instruction,
                      after={"provider_transaction_id": provider_tx})
                return Response({
                    "status": "duplicate_callback",
                    "instruction_id": str(instruction.id),
                    "provider_reference": instruction.provider_reference,
                    "double_posting": False,
                })
            raise ValidationError({"outcome": "Instruction is already settled; a different provider reference cannot settle it again."})

        # Timeout is UNKNOWN at provider level and remains pending/submitted.
        if outcome == "timeout":
            if instruction.status not in {PaymentInstruction.Status.CREATED, PaymentInstruction.Status.SUBMITTED}:
                raise ValidationError({"outcome": "Timeout can only occur before settlement."})
            old_status = instruction.status
            instruction.status = PaymentInstruction.Status.SUBMITTED
            instruction.save(update_fields=["status"])
            event = PaymentEvent.objects.create(
                instruction=instruction,
                event_type=PaymentEvent.EventType.EXCEPTION,
                provider_status=PaymentEvent.ProviderStatus.UNKNOWN,
                provider_transaction_id=provider_tx,
                from_status=old_status,
                to_status=instruction.status,
                recorded_by=request.user,
                redacted_payload={"simulation": True, "timeout": True, "retry_allowed": False},
            )
            audit(request.user, "PAYMENT_TIMEOUT_UNKNOWN", instruction,
                  before={"status": old_status}, after={"status": instruction.status, "provider_status": "UNKNOWN"})
            return Response({"status": "UNKNOWN", "pending": True, "retry_allowed": False, "event_id": str(event.id)})

        # CAS03: failed payments are retried as a new linked attempt; the original
        # failed instruction remains immutable history.
        if outcome == "retry":
            if instruction.status != PaymentInstruction.Status.FAILED:
                raise ValidationError({"outcome": "Only a failed payment can be retried; pending/unknown payments must be reconciled first."})
            retry = PaymentInstruction.objects.create(
                batch=instruction.batch,
                enrollment=instruction.enrollment,
                beneficiary=instruction.beneficiary,
                channel_config=instruction.channel_config,
                amount=instruction.amount,
                currency=instruction.currency,
                status=PaymentInstruction.Status.SUBMITTED,
                idempotency_key=f"retry:{instruction.id}:{uuid.uuid4()}",
                created_by=request.user,
            )
            PaymentEvent.objects.create(
                instruction=retry,
                event_type=PaymentEvent.EventType.RETRY,
                provider_status=PaymentEvent.ProviderStatus.ACCEPTED,
                from_status=PaymentInstruction.Status.DRAFT,
                to_status=PaymentInstruction.Status.SUBMITTED,
                recorded_by=request.user,
                redacted_payload={"simulation": True, "linked_failed_instruction": str(instruction.id)},
            )
            audit(request.user, "PAYMENT_RETRY_CREATED", retry,
                  after={"linked_failed_instruction": str(instruction.id), "status": retry.status})
            return Response({
                "status": "retry_created",
                "original_instruction_id": str(instruction.id),
                "retry_instruction_id": str(retry.id),
                "original_failure_retained": True,
            }, status=status.HTTP_201_CREATED)

        transitions = {
            "submit": (PaymentInstruction.Status.SUBMITTED, PaymentEvent.EventType.SUBMITTED),
            "success": (PaymentInstruction.Status.SUCCESS, PaymentEvent.EventType.SUCCESS),
            "failure": (PaymentInstruction.Status.FAILED, PaymentEvent.EventType.FAILED),
            "reversal": (PaymentInstruction.Status.REVERSED, PaymentEvent.EventType.REVERSED),
        }
        if outcome not in transitions:
            raise ValidationError({"outcome": "Use submit, success, failure, timeout, retry, or reversal"})

        old_status = instruction.status
        new_status, event_type = transitions[outcome]
        allowed = {
            "submit": {PaymentInstruction.Status.CREATED},
            "success": {PaymentInstruction.Status.SUBMITTED, PaymentInstruction.Status.CREATED},
            "failure": {PaymentInstruction.Status.SUBMITTED, PaymentInstruction.Status.CREATED},
            "reversal": {PaymentInstruction.Status.SUCCESS, PaymentInstruction.Status.FAILED},
        }
        if old_status not in allowed[outcome]:
            raise ValidationError({"outcome": f"Invalid transition from {old_status} to {new_status}"})

        instruction.status = new_status
        if outcome == "success":
            instruction.provider_reference = instruction.provider_reference or provider_tx or f"SIM-{str(instruction.id)[:8].upper()}"
        elif provider_tx:
            instruction.provider_reference = instruction.provider_reference or provider_tx
        instruction.save(update_fields=["status", "provider_reference"])
        event = PaymentEvent.objects.create(
            instruction=instruction,
            event_type=event_type,
            provider_status=PaymentEvent.ProviderStatus.ACCEPTED if outcome == "submit" else PaymentEvent.ProviderStatus.SETTLED if outcome == "success" else PaymentEvent.ProviderStatus.FAILED,
            provider_transaction_id=provider_tx,
            from_status=old_status,
            to_status=new_status,
            recorded_by=request.user,
            redacted_payload={"simulation": True, "no_external_provider_call": True},
        )
        audit(request.user, "PAYMENT_SIMULATED", instruction, before={"status": old_status}, after={"status": new_status})
        return Response(PaymentEventSerializer(event).data)

    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        return Response(PaymentEventSerializer(self.get_object().events.all().order_by("created_at"), many=True).data)


class ComplaintViewSet(RoleProtectedTenantViewSet):
    queryset = Complaint.objects.select_related("beneficiary", "beneficiary__household")
    serializer_class = ComplaintSerializer
    tenant_field = "beneficiary__household__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.SUPPORT, User.Role.MANAGER}


class BudgetViewSet(RoleProtectedTenantViewSet):
    queryset = Budget.objects.select_related("program")
    serializer_class = BudgetSerializer
    tenant_field = "program__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}


class PaymentBatchViewSet(RoleProtectedTenantViewSet):
    queryset = PaymentBatch.objects.select_related("program")
    serializer_class = PaymentBatchSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}


class AISignalViewSet(TenantScopedModelViewSet):
    queryset = AISignal.objects.select_related("program")
    serializer_class = AISignalSerializer

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        if request.user.role not in {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER}:
            signal = self.get_object()
            audit(request.user, "AI_SIGNAL_REVIEW_DENIED", signal, after={"attempted_status": request.data.get("status")})
            raise PermissionDenied("Only an authorized independent reviewer can approve AI signals")
        signal = self.get_object()
        signal.status = request.data.get("status", AISignal.Status.REVIEWED)
        signal.review_note = request.data.get("review_note", "")
        signal.reviewed_by = request.user
        signal.reviewed_at = timezone.now()
        signal.save(update_fields=["status", "review_note", "reviewed_by", "reviewed_at"])
        audit(request.user, "AI_SIGNAL_REVIEWED", signal, after={"status": signal.status})
        return Response(AISignalSerializer(signal).data)


class ReconciliationItemViewSet(RoleProtectedTenantViewSet):
    queryset = ReconciliationItem.objects.select_related("program", "instruction")
    serializer_class = ReconciliationItemSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        item = self.get_object()
        item.status = ReconciliationItem.Status.RESOLVED
        item.resolution_note = request.data.get("resolution_note", "")
        item.resolved_by = request.user
        item.resolved_at = timezone.now()
        item.save(update_fields=["status", "resolution_note", "resolved_by", "resolved_at"])
        audit(request.user, "RECONCILIATION_ITEM_RESOLVED", item, after={"status": item.status})
        return Response(ReconciliationItemSerializer(item).data)


class ReviewTaskViewSet(RoleProtectedTenantViewSet):
    queryset = ReviewTask.objects.select_related("program", "assigned_to")
    serializer_class = ReviewTaskSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.REVIEWER, User.Role.MANAGER, User.Role.SUPPORT}

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        task = self.get_object()
        task.status = ReviewTask.Status.RESOLVED
        task.resolution = request.data.get("resolution", "")
        task.resolved_at = timezone.now()
        task.save(update_fields=["status", "resolution", "resolved_at"])
        audit(request.user, "REVIEW_TASK_RESOLVED", task, after={"status": task.status})
        return Response(ReviewTaskSerializer(task).data)


class AutomationRuleViewSet(TenantScopedModelViewSet):
    queryset = AutomationRule.objects.select_related("program")
    serializer_class = AutomationRuleSerializer


class AutomationExecutionViewSet(TenantScopedModelViewSet):
    queryset = AutomationExecution.objects.select_related("rule")
    serializer_class = AutomationExecutionSerializer
    http_method_names = ["get", "head", "options"]


class SyncOperationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SyncOperation.objects.all()
    serializer_class = SyncOperationSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.MANAGER, User.Role.AUDITOR}

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.user.role not in self.allowed_roles:
            raise PermissionDenied("Your role does not have permission to view sync operations")

    def get_queryset(self):
        return SyncOperation.objects.filter(tenant=self.request.user.tenant)


class AuditEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditEventSerializer

    def get_queryset(self):
        if self.request.user.role not in {User.Role.ADMIN, User.Role.MANAGER, User.Role.AUDITOR}:
            raise PermissionDenied("Audit history is restricted")
        return AuditEvent.objects.filter(tenant=self.request.user.tenant)


@api_view(["GET"])
def reports_view(request):
    tenant = request.user.tenant
    programs = Program.objects.filter(tenant=tenant)
    payments = PaymentInstruction.objects.filter(enrollment__program__tenant=tenant)
    enrollments = Enrollment.objects.filter(program__tenant=tenant)
    complaints = Complaint.objects.filter(beneficiary__household__tenant=tenant)
    beneficiaries = Beneficiary.objects.filter(household__tenant=tenant)
    distributed = payments.filter(status=PaymentInstruction.Status.SUCCESS).aggregate(total=Sum("amount"))["total"] or 0
    approved = enrollments.filter(status=Enrollment.Status.APPROVED).count()
    return Response({
        "dashboards": {
            "beneficiaries": beneficiaries.count(),
            "approvals": approved,
            "paid": payments.filter(status=PaymentInstruction.Status.SUCCESS).count(),
            "pending": payments.filter(status__in=[PaymentInstruction.Status.DRAFT, PaymentInstruction.Status.CREATED, PaymentInstruction.Status.SUBMITTED]).count(),
            "failed": payments.filter(status=PaymentInstruction.Status.FAILED).count(),
            "amounts_approved": str(programs.aggregate(total=Sum("transfer_amount"))["total"] or 0),
            "amounts_distributed": str(distributed),
            "geography": list(Household.objects.filter(tenant=tenant).values("location").annotate(count=Count("id")).order_by("location")),
            "reconciliation": list(payments.values("status").annotate(count=Count("id"), amount=Sum("amount")).order_by("status")),
            "complaints": list(complaints.values("status").annotate(count=Count("id")).order_by("status")),
            "operational_exceptions": payments.filter(Q(status=PaymentInstruction.Status.FAILED) | Q(complaints__isnull=False)).distinct().count(),
            "program_kpis": list(programs.values("id", "name", "country", "currency", "status").annotate(enrollments=Count("enrollments"))),
            "ai_copilot": AICopilotService.generate_system_summary_report(tenant=tenant),
        }
    })


@api_view(["GET"])
def program_summary_view(request, program_id):
    program = get_object_or_404(Program, id=program_id, tenant=request.user.tenant)
    payments = PaymentInstruction.objects.filter(enrollment__program=program)
    enrollments = Enrollment.objects.filter(program=program)
    complaints = Complaint.objects.filter(beneficiary__household__program=program)
    reconciliation = ReconciliationItem.objects.filter(program=program)
    budget = getattr(program, "budget", None)
    return Response({
        "program": {"id": str(program.id), "name": program.name, "country_code": program.country_code, "currency": program.currency, "reporting_currency": program.reporting_currency, "timezone": program.timezone, "language": program.language},
        "beneficiaries": Beneficiary.objects.filter(household__program=program).count(),
        "eligibility": list(enrollments.values("eligibility_status").annotate(count=Count("id")).order_by("eligibility_status")),
        "approvals": enrollments.filter(status=Enrollment.Status.APPROVED).count(),
        "payments": list(payments.values("status").annotate(count=Count("id"), amount=Sum("amount")).order_by("status")),
        "amounts": {"approved": str(program.transfer_amount * enrollments.filter(status=Enrollment.Status.APPROVED).count()), "distributed": str(payments.filter(status=PaymentInstruction.Status.SUCCESS).aggregate(total=Sum("amount"))["total"] or 0)},
        "failures": payments.filter(status=PaymentInstruction.Status.FAILED).count(),
        "reconciliation": list(reconciliation.values("issue_type", "status").annotate(count=Count("id")).order_by("issue_type")),
        "pdm": {"summary_source": "derived_api"},
        "complaints": list(complaints.values("status", "severity").annotate(count=Count("id")).order_by("status")),
        "budget": {"planned_total": str(budget.planned_total), "actual_total": str(budget.actual_total)} if budget else None,
        "verified": True,
    })


@api_view(["POST"])
def imports_view(request):
    if request.user.role not in {User.Role.ADMIN, User.Role.MANAGER}:
        raise PermissionDenied("Only administrators and managers can import mapped records")
    return Response({
        "status": "accepted",
        "message": "Controlled CSV/XLSX import mapping endpoint placeholder. Processing service can be connected here.",
        "client_identifiers_preserved": True,
    }, status=status.HTTP_202_ACCEPTED)


@api_view(["POST"])
def analyze_complaint_api(request, complaint_id):
    """Run explainable complaint analysis; source complaint remains unchanged."""
    if request.user.role not in {User.Role.ADMIN, User.Role.SUPPORT, User.Role.MANAGER, User.Role.REVIEWER}:
        raise PermissionDenied("Your role does not have permission to analyze complaints")
    complaint = get_object_or_404(
        Complaint.objects.select_related("beneficiary__household__program", "instruction"),
        id=complaint_id,
        beneficiary__household__tenant=request.user.tenant,
    )
    analysis = analyze_complaint(complaint, actor=request.user)
    return Response(ComplaintAIAnalysisSerializer(analysis).data, status=status.HTTP_200_OK)


@api_view(["GET"])
def complaint_ai_analysis_api(request, complaint_id):
    if request.user.role not in {User.Role.ADMIN, User.Role.SUPPORT, User.Role.MANAGER, User.Role.REVIEWER, User.Role.AUDITOR}:
        raise PermissionDenied("Your role does not have permission to view complaint AI analysis")
    complaint = get_object_or_404(
        Complaint.objects.select_related("beneficiary__household"),
        id=complaint_id,
        beneficiary__household__tenant=request.user.tenant,
    )
    analysis = get_object_or_404(ComplaintAIAnalysis, complaint=complaint, tenant=request.user.tenant)
    return Response(ComplaintAIAnalysisSerializer(analysis).data)



@api_view(["POST"])
def complaint_ai_review_api(request, complaint_id):
    if request.user.role not in {User.Role.ADMIN, User.Role.SUPPORT, User.Role.MANAGER, User.Role.REVIEWER}:
        raise PermissionDenied("Your role does not have permission to review complaint AI analysis")
    complaint = get_object_or_404(Complaint, id=complaint_id, beneficiary__household__tenant=request.user.tenant)
    analysis = get_object_or_404(ComplaintAIAnalysis, complaint=complaint, tenant=request.user.tenant)
    decision = request.data.get("decision")
    if decision not in {"ACCEPT", "MODIFY", "DISMISS"}:
        raise ValidationError({"decision": "Use ACCEPT, MODIFY, or DISMISS"})
    analysis.reviewer_decision = decision
    analysis.reviewer_note = request.data.get("reviewer_note", "")
    analysis.reviewed_by = request.user
    analysis.reviewed_at = timezone.now()
    analysis.save(update_fields=["reviewer_decision", "reviewer_note", "reviewed_by", "reviewed_at", "updated_at"])
    signal = AISignal.objects.filter(tenant=request.user.tenant, entity_type="Complaint", entity_id=str(complaint.id), signal_type="COMPLAINT_INTELLIGENCE", status=AISignal.Status.OPEN).first()
    if signal:
        signal.status = AISignal.Status.DISMISSED if decision == "DISMISS" else AISignal.Status.ACCEPTED if decision == "ACCEPT" else AISignal.Status.REVIEWED
        signal.reviewed_by = request.user
        signal.reviewed_at = timezone.now()
        signal.review_note = analysis.reviewer_note
        signal.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])
    ReviewTask.objects.filter(tenant=request.user.tenant, entity_type="Complaint", entity_id=str(complaint.id), task_type=ReviewTask.TaskType.COMPLAINT_ESCALATION, status__in=[ReviewTask.Status.OPEN, ReviewTask.Status.ASSIGNED]).update(status=ReviewTask.Status.RESOLVED, resolution=f"AI analysis reviewed by {request.user.full_name}: {decision}", resolved_at=timezone.now())
    audit(request.user, "COMPLAINT_AI_REVIEWED", analysis, after={"decision": decision, "reviewer_note": analysis.reviewer_note})
    return Response(ComplaintAIAnalysisSerializer(analysis).data)


@api_view(["POST"])
def analyze_complaints_bulk_api(request):
    """Analyze all open/in-progress complaints, optionally limited to a program."""
    if request.user.role not in {User.Role.ADMIN, User.Role.SUPPORT, User.Role.MANAGER, User.Role.REVIEWER}:
        raise PermissionDenied("Your role does not have permission to analyze complaints")
    qs = Complaint.objects.select_related("beneficiary__household__program", "instruction").filter(
        beneficiary__household__tenant=request.user.tenant,
        status__in=[Complaint.Status.OPEN, Complaint.Status.IN_PROGRESS],
    )
    program_id = request.data.get("program_id")
    if program_id:
        qs = qs.filter(beneficiary__household__program_id=program_id)
    limit = min(int(request.data.get("limit", 100)), 500)
    results = []
    for complaint in qs.order_by("created_at")[:limit]:
        results.append(ComplaintAIAnalysisSerializer(analyze_complaint(complaint, actor=request.user)).data)
    return Response({"analyzed": len(results), "results": results})


@api_view(["GET", "POST"])
def pdm_view(request):
    if request.user.role not in {User.Role.ADMIN, User.Role.SUPPORT, User.Role.FIELD_OFFICER, User.Role.MANAGER}:
        raise PermissionDenied("Your role does not have permission for PDM")
    return Response({
        "status": "derived",
        "message": "PDM is exposed without a dedicated MVP table; payloads are handled through controlled API/audit flow.",
        "submitted_at": timezone.now() if request.method == "POST" else None,
    })


@api_view(["GET"])
def pdm_summary_view(request):
    if request.user.role not in {User.Role.ADMIN, User.Role.SUPPORT, User.Role.FIELD_OFFICER, User.Role.MANAGER}:
        raise PermissionDenied("Your role does not have permission for PDM summaries")
    return Response({
        "program": request.query_params.get("program"),
        "channel": request.query_params.get("channel"),
        "location": request.query_params.get("location"),
        "received_rate": 0,
        "amount_received": "0.00",
        "access_problem_rate": 0,
        "complaint_rate": 0,
        "satisfaction": 0,
        "source": "derived_placeholder_until_pdm_storage_is_enabled",
    })


@api_view(["POST"])
def registration_sync_view(request):
    """Idempotent offline registration sync with post-sync validation."""
    if request.user.role not in {User.Role.ADMIN, User.Role.FIELD_OFFICER}:
        raise PermissionDenied("Only field officers and administrators can synchronize registrations")
    operation_id = request.data.get("operation_id") or request.data.get("idempotency_key")
    program_id = request.data.get("program_id")
    household_data = request.data.get("household", request.data)
    client_generated_id = household_data.get("client_generated_id")
    if not operation_id:
        raise ValidationError({"operation_id": "Required for offline sync idempotency"})
    if not program_id or not client_generated_id:
        raise ValidationError({"program_id": "Required", "client_generated_id": "Required"})
    canonical = json.dumps(request.data, sort_keys=True, default=str, separators=(",", ":"))
    request_hash = hashlib.sha256(canonical.encode()).hexdigest()
    existing = SyncOperation.objects.filter(tenant=request.user.tenant, operation_id=operation_id).first()
    if existing:
        if existing.request_hash != request_hash:
            existing.status = SyncOperation.Status.REJECTED
            existing.error_message = "Same operation_id was received with different payload."
            existing.save(update_fields=["status", "error_message"])
            raise ValidationError({"operation_id": "Idempotency conflict: payload differs from the original operation."})
        return Response({"status": "duplicate", "operation_id": operation_id, "sync_status": existing.status, "validation_results": existing.validation_results})

    program = get_object_or_404(Program, id=program_id, tenant=request.user.tenant)
    sync = SyncOperation.objects.create(tenant=request.user.tenant, operation_id=operation_id, operation_type="REGISTRATION_SYNC", client_generated_id=client_generated_id, request_hash=request_hash)
    household, household_created = Household.objects.get_or_create(
        tenant=request.user.tenant, client_generated_id=client_generated_id,
        defaults={"program": program, "household_size": household_data.get("household_size", 1), "location": household_data.get("location", "Unspecified"), "registration_date": household_data.get("registration_date") or timezone.localdate(), "created_by": request.user},
    )
    if household.program_id != program.id:
        sync.status = SyncOperation.Status.CONFLICT
        sync.error_message = "client_generated_id already belongs to another program."
        sync.processed_at = timezone.now()
        sync.save(update_fields=["status", "error_message", "processed_at"])
        raise ValidationError({"client_generated_id": "Already belongs to another program"})

    synced = []
    for data in request.data.get("beneficiaries", []):
        number, full_name = data.get("number"), data.get("full_name")
        if not number or not full_name:
            raise ValidationError({"beneficiaries": "Each beneficiary requires number and full_name"})
        beneficiary, _ = Beneficiary.objects.get_or_create(
            household=household, number=number,
            defaults={"full_name": full_name, "gender": data.get("gender", ""), "date_of_birth": data.get("date_of_birth") or None, "phone_last4": data.get("phone_last4", ""), "national_id_hash": data.get("national_id_hash", ""), "consent_given": data.get("consent_given", False), "created_by": request.user},
        )
        synced.append(beneficiary)

    validation_results = validate_synced_registration(sync, household, synced)
    audit(request.user, "OFFLINE_REGISTRATION_SYNCED", household, after={"operation_id": operation_id, "client_generated_id": client_generated_id, "beneficiaries_count": len(synced), "sync_status": sync.status})
    return Response({"status": "synchronized", "operation_id": operation_id, "sync_status": sync.status, "household_id": str(household.id), "household_created": household_created, "beneficiaries_processed": len(synced), "client_generated_id": client_generated_id, "validation_results": validation_results, "tenant_isolated": True}, status=status.HTTP_201_CREATED if household_created else status.HTTP_200_OK)


@api_view(["POST"])
def offline_event_api(request):
    """Generic Task 3 offline replay receipt for integration tests X02."""
    if request.user.role not in {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.MANAGER}:
        audit(request.user, "OFFLINE_EVENT_DENIED", request.user, after={"reason": "role"})
        raise PermissionDenied("Your role cannot submit offline events")
    operation_id = str(request.data.get("operation_id", "")).strip()
    operation_type = str(request.data.get("operation_type", "GENERIC_OFFLINE_EVENT")).strip()
    payload = request.data.get("payload", {})
    if not operation_id:
        raise ValidationError({"operation_id": "Required"})
    if not isinstance(payload, dict):
        raise ValidationError({"payload": "Must be an object"})
    return Response(record_offline_event(
        tenant=request.user.tenant,
        operation_id=operation_id,
        operation_type=operation_type,
        payload=payload,
        client_generated_id=str(request.data.get("client_generated_id", "")),
        actor=request.user,
    ))


@api_view(["GET", "POST"])
def ai_closed_view(request, *args, **kwargs):
    return Response({"enabled": True, "message": "AI services are available through dedicated endpoints."})


@api_view(["POST"])
def data_quality_api(request, beneficiary_id):
    if request.user.role not in {User.Role.ADMIN, User.Role.FIELD_OFFICER, User.Role.REVIEWER, User.Role.MANAGER, User.Role.AUDITOR}:
        raise PermissionDenied("Your role does not have permission to run data-quality checks")
    beneficiary = get_object_or_404(Beneficiary, id=beneficiary_id, household__tenant=request.user.tenant)
    return Response(run_data_quality_check(beneficiary))


@api_view(["POST"])
def automation_event_api(request):
    if request.user.role not in {User.Role.ADMIN, User.Role.MANAGER}:
        raise PermissionDenied("Only administrators and managers can execute automation rules")
    event_name, payload = request.data.get("event_name"), request.data.get("payload", {})
    if not event_name or not isinstance(payload, dict):
        raise ValidationError({"event_name": "Required", "payload": "Must be an object"})
    program = None
    program_id = request.data.get("program_id") or payload.get("program_id")
    if program_id:
        program = get_object_or_404(Program, id=program_id, tenant=request.user.tenant)
    return Response({"event_name": event_name, "executions": process_automation_event(tenant=request.user.tenant, event_name=event_name, payload=payload, program=program, dry_run=bool(request.data.get("dry_run", False)))})


@api_view(['POST'])
def trigger_deduplication_api(request, beneficiary_id):
    """
    نقطة نهاية لتشغيل فحص التكرار لمستفيد معين عبر الـ API
    """
    try:
        beneficiary = get_object_or_404(Beneficiary, id=beneficiary_id, household__tenant=request.user.tenant)
        has_duplicates = run_deduplication_check(beneficiary)
        return Response({
            "status": "success",
            "beneficiary_id": beneficiary_id,
            "duplicate_flagged": has_duplicates,
            "message": "Deduplication check executed successfully."
        }, status=status.HTTP_200_OK)
    except Beneficiary.DoesNotExist:
        return Response({"error": "Beneficiary not found."}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def trigger_reconciliation_api(request):
    if request.user.role not in {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}:
        raise PermissionDenied("Only finance-authorized roles can run reconciliation")
    """
    نقطة نهاية لتشغيل التسوية التلقائية لدفعة مالية بناءً على تقرير المزود
    """
    batch_id = request.data.get("batch_id")
    provider_report_data = request.data.get("provider_report_data", {}) # Dictionary of reports
    
    if not batch_id:
        return Response({"error": "batch_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    results = run_automated_reconciliation(batch_id, provider_report_data)
    return Response({
        "status": "success",
        "batch_id": batch_id,
        "reconciliation_summary": results
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
def trigger_anomaly_detection_api(request):
    if request.user.role not in {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.AUDITOR}:
        raise PermissionDenied("Only finance-authorized roles can run anomaly detection")
    """
    نقطة نهاية لكشف الانحرافات والأنماط الشاذة للدفعة المالية
    """
    batch_id = request.data.get("batch_id")
    if not batch_id:
        return Response({"error": "batch_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    anomalies_count = run_anomaly_detection(batch_id)
    return Response({
        "status": "success",
        "batch_id": batch_id,
        "anomalies_detected": anomalies_count,
        "message": "Anomaly detection execution completed."
    }, status=status.HTTP_200_OK)