from decimal import Decimal

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



class AISignalSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)

    class Meta:
        model = AISignal
        fields = "__all__"
        read_only_fields = ["tenant", "reviewed_by", "reviewed_at", "created_at"]


class ReviewTaskSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.full_name", read_only=True)

    class Meta:
        model = ReviewTask
        fields = "__all__"
        read_only_fields = ["tenant", "created_at", "resolved_at"]

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
    class Meta:
        model = AutomationExecution
        fields = "__all__"
        read_only_fields = ["tenant", "created_at", "status", "planned_action"]
