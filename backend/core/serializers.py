from django.contrib.auth import authenticate
from django.utils import timezone
from rest_framework import serializers

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
    Tenant,
    User,
)


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = "__all__"


class UserSerializer(serializers.ModelSerializer):
    tenant = TenantSerializer(read_only=True)
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ["id", "tenant", "email", "full_name", "role", "is_active", "is_staff", "created_at", "password"]
        read_only_fields = ["id", "tenant", "created_at"]

    def create(self, validated_data):
        password = validated_data.pop("password", None) or "ChangeMe123!"
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(username=attrs["email"], password=attrs["password"])
        if not user or not user.is_active:
            raise serializers.ValidationError("Invalid credentials")
        attrs["user"] = user
        return attrs


class ProgramSerializer(serializers.ModelSerializer):
    class Meta:
        model = Program
        fields = "__all__"
        read_only_fields = ["tenant", "created_by", "created_at"]


class HouseholdSerializer(serializers.ModelSerializer):
    class Meta:
        model = Household
        fields = "__all__"
        read_only_fields = ["tenant", "created_by", "created_at"]

    def validate_program(self, program):
        if program.tenant_id != self.context["request"].user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        return program


class BeneficiarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Beneficiary
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]

    def validate_household(self, household):
        if household.tenant_id != self.context["request"].user.tenant_id:
            raise serializers.ValidationError("Household belongs to another tenant")
        return household


class EnrollmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Enrollment
        fields = "__all__"
        read_only_fields = ["approved_by", "approved_at", "enrolled_at"]

    def validate(self, attrs):
        beneficiary = attrs.get("beneficiary") or self.instance.beneficiary
        program = attrs.get("program") or self.instance.program
        if beneficiary.household.tenant_id != program.tenant_id:
            raise serializers.ValidationError("Beneficiary and program must belong to the same tenant")
        return attrs

    def update(self, instance, validated_data):
        status = validated_data.get("status")
        if status in {Enrollment.Status.APPROVED, Enrollment.Status.REJECTED}:
            instance.approved_by = self.context["request"].user
            instance.approved_at = timezone.now()
        return super().update(instance, validated_data)


class PaymentChannelConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentChannelConfig
        fields = "__all__"


class PaymentInstructionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentInstruction
        fields = "__all__"
        read_only_fields = ["created_by", "created_at", "status", "provider_reference", "idempotency_key"]

    def validate(self, attrs):
        enrollment = attrs["enrollment"]
        beneficiary = attrs["beneficiary"]
        channel_config = attrs["channel_config"]
        if enrollment.status != Enrollment.Status.APPROVED:
            raise serializers.ValidationError("Payment instruction requires an approved enrollment")
        if beneficiary.pk != enrollment.beneficiary.pk:
            raise serializers.ValidationError("Beneficiary must match enrollment")
        if channel_config.program.pk != enrollment.program.pk:
            raise serializers.ValidationError("Channel must belong to the enrollment program")
        if enrollment.program.tenant.pk != self.context["request"].user.tenant.pk:
            raise serializers.ValidationError("Enrollment belongs to another tenant")
        return attrs


class PaymentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentEvent
        fields = "__all__"


class PaymentBatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentBatch
        fields = "__all__"
        read_only_fields = ["tenant", "created_by", "created_at"]


class ComplaintSerializer(serializers.ModelSerializer):
    class Meta:
        model = Complaint
        fields = "__all__"
        read_only_fields = ["created_by", "created_at", "resolved_at"]

    def validate_beneficiary(self, beneficiary):
        if beneficiary.household.tenant_id != self.context["request"].user.tenant_id:
            raise serializers.ValidationError("Beneficiary belongs to another tenant")
        return beneficiary

    def update(self, instance, validated_data):
        if validated_data.get("status") in {Complaint.Status.RESOLVED, Complaint.Status.CLOSED}:
            instance.resolved_at = timezone.now()
        return super().update(instance, validated_data)


class ComplaintAIAnalysisSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComplaintAIAnalysis
        fields = "__all__"
        read_only_fields = ["id", "tenant", "created_at", "updated_at"]


class BudgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Budget
        fields = "__all__"

    def validate_line_items(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Line items must be a list")
        for item in value:
            for field in ["key", "category", "planned_amount", "actual_amount"]:
                if field not in item:
                    raise serializers.ValidationError(f"Line item missing {field}")
            if float(item["planned_amount"]) < 0 or float(item["actual_amount"]) < 0:
                raise serializers.ValidationError("Line item amounts must be non-negative")
        return value

    def validate_program(self, program):
        if program.tenant_id != self.context["request"].user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        return program


class AuditEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", read_only=True)

    class Meta:
        model = AuditEvent
        fields = "__all__"
        read_only_fields = [
            "id",
            "tenant",
            "actor",
            "action",
            "entity_type",
            "entity_id",
            "before",
            "after",
            "correlation_id",
            "created_at",
        ]


class AISignalSerializer(serializers.ModelSerializer):
    class Meta:
        model = AISignal
        fields = "__all__"
        read_only_fields = ["tenant", "reviewed_by", "reviewed_at", "created_at"]


class ReconciliationItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReconciliationItem
        fields = "__all__"
        read_only_fields = ["tenant", "resolved_by", "resolved_at", "created_at"]


class ReviewTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewTask
        fields = "__all__"
        read_only_fields = ["tenant", "created_at", "resolved_at"]


class AutomationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationRule
        fields = "__all__"
        read_only_fields = ["tenant", "created_at"]


class AutomationExecutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationExecution
        fields = "__all__"
        read_only_fields = ["tenant", "created_at", "status", "planned_action"]


class SyncOperationSerializer(serializers.ModelSerializer):
    class Meta:
        model = SyncOperation
        fields = "__all__"
        read_only_fields = ["id", "tenant", "created_at", "processed_at", "status", "validation_results", "error_message", "request_hash"]
