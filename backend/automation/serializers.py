from decimal import Decimal
from uuid import UUID

from django.contrib.auth import authenticate
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import serializers

from accounts.models import IdempotencyRecord, Tenant, User
from assistance.models import Beneficiary, CashEntitlement, Enrollment, Household, HouseholdEligibility, HouseholdEnrollment
from assistance.serializer_helpers import (
    approved_household_modality,
    household_member_count,
    household_member_details,
    household_reference,
    national_id_digest,
    national_id_reference,
    normalize_national_id,
    normalize_phone_number,
    safe_beneficiary_reference,
)
from automation.models import AISignal, AutomationExecution, AutomationRule, ReviewTask
from operations.models import (
    Complaint,
    ComplaintAIAnalysis,
    DistributionAllocation,
    DistributionEvent,
    DistributionIssue,
    NFIEntitlement,
    NFIItem,
    PaymentDeliveryReview,
    StockMovement,
    Warehouse,
)
from payments.models import PaymentBatch, PaymentChannelConfig, PaymentEvent, PaymentInstruction, ReconciliationItem
from programs.models import ActivityDependency, Budget, Program, ProgramActivity
from reporting.models import AuditEvent


def _entity_label(entity_type, entity_id, tenant):
    """Resolve polymorphic AI/review references without exposing raw UUIDs."""
    kind = str(entity_type or "record").strip().upper()
    identifier = str(entity_id or "").strip()
    try:
        UUID(identifier)
        is_uuid = True
    except (TypeError, ValueError, AttributeError):
        is_uuid = False

    if kind == "BENEFICIARY" and is_uuid:
        beneficiary = Beneficiary.objects.filter(
            pk=identifier,
            household__tenant=tenant,
        ).first()
        if beneficiary:
            return f"{beneficiary.full_name} ({safe_beneficiary_reference(beneficiary)})"

    if kind == "COMPLAINT" and is_uuid:
        complaint = Complaint.objects.select_related("beneficiary").filter(
            pk=identifier,
            beneficiary__household__tenant=tenant,
        ).first()
        if complaint:
            return f"Complaint for {complaint.beneficiary.full_name}"

    if kind == "PAYMENT_INSTRUCTION" and is_uuid:
        instruction = PaymentInstruction.objects.select_related(
            "cash_entitlement__household",
            "enrollment__beneficiary__household",
        ).filter(
            Q(cash_entitlement__program__tenant=tenant)
            | Q(enrollment__program__tenant=tenant),
            pk=identifier,
        ).first()
        if instruction:
            household = (
                instruction.cash_entitlement.household
                if instruction.cash_entitlement_id
                else instruction.enrollment.beneficiary.household
                if instruction.enrollment_id and instruction.enrollment.beneficiary_id
                else None
            )
            return f"Payment for {household_reference(household)}"

    if kind == "ENROLLMENT" and is_uuid:
        enrollment = Enrollment.objects.select_related("beneficiary").filter(
            pk=identifier,
            program__tenant=tenant,
        ).first()
        if enrollment and enrollment.beneficiary:
            return f"Enrollment for {enrollment.beneficiary.full_name}"

    label = kind.replace("_", " ").title()
    return (
        f"{label}: {identifier}"
        if identifier and "-" not in identifier
        else f"{label} record"
    )



class AISignalSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    entity_label = serializers.SerializerMethodField()
    reviewed_by_name = serializers.CharField(source="reviewed_by.full_name", read_only=True)

    class Meta:
        model = AISignal
        fields = "__all__"
        read_only_fields = ["tenant", "reviewed_by", "reviewed_at", "created_at"]

    def get_entity_label(self, signal):
        return _entity_label(signal.entity_type, signal.entity_id, signal.tenant)


class ReviewTaskSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.full_name", read_only=True)
    entity_label = serializers.SerializerMethodField()

    class Meta:
        model = ReviewTask
        fields = "__all__"
        read_only_fields = ["tenant", "created_at", "resolved_at"]

    def get_entity_label(self, task):
        return _entity_label(task.entity_type, task.entity_id, task.tenant)

    def validate(self, attrs):
        user = self.context["request"].user
        program = attrs.get("program") or getattr(self.instance, "program", None)
        assigned_to = attrs.get("assigned_to")
        if program and not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError({"program": "Program belongs to another tenant"})
        if assigned_to and program and assigned_to.tenant_id != program.tenant_id:
            raise serializers.ValidationError({"assigned_to": "Assignee belongs to another tenant"})
        return attrs


class AutomationRuleSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)

    class Meta:
        model = AutomationRule
        fields = "__all__"
        read_only_fields = ["tenant", "created_at"]

    def validate(self, attrs):
        user = self.context["request"].user
        program = attrs.get("program") or getattr(self.instance, "program", None)
        if program and not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError({"program": "Program belongs to another tenant"})
        return attrs


class AutomationExecutionSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="rule.program.name", read_only=True)
    event_name = serializers.CharField(source="rule.event_name", read_only=True)
    action_name = serializers.CharField(source="rule.action_name", read_only=True)

    class Meta:
        model = AutomationExecution
        fields = "__all__"
        read_only_fields = ["tenant", "created_at", "status", "planned_action"]
