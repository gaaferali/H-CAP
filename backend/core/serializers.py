from django.contrib.auth import authenticate
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework import serializers
import re

from .models import (
    AISignal,
    AuditEvent,
    AutomationExecution,
    AutomationRule,
    ActivityDependency,
    Beneficiary,
    Budget,
    Complaint,
    Enrollment,
    Household,
    PaymentChannelConfig,
    PaymentEvent,
    PaymentInstruction,
    PaymentBatch,
    ProgramActivity,
    Program,
    ReconciliationItem,
    ReviewTask,
    Tenant,
    User,
)


def normalize_phone_number(value):
    normalized = re.sub(r"[\s-]+", "", str(value or "").strip())
    if not normalized:
        return ""
    if not re.fullmatch(r"\+?[0-9]{6,29}", normalized):
        raise serializers.ValidationError("Use a valid international phone number with digits, spaces, hyphens, and an optional leading +.")
    return normalized


def normalize_national_id(value):
    normalized = re.sub(r"[\s-]+", "", str(value or "").strip()).upper()
    if not normalized:
        raise serializers.ValidationError("National ID is required.")
    if not re.fullmatch(r"[A-Z0-9]{3,80}", normalized):
        raise serializers.ValidationError("National ID must contain letters or numbers and may include spaces or hyphens.")
    return normalized


def national_id_digest(national_id):
    return salted_hmac("hcap.beneficiary.national-id", national_id).hexdigest()


def national_id_reference(national_id):
    return f"NID-{national_id_digest(national_id).upper()}"


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = "__all__"


class UserSerializer(serializers.ModelSerializer):
    tenant = TenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(source="tenant", queryset=Tenant.objects.all(), write_only=True, required=False)
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ["id", "tenant", "tenant_id", "email", "full_name", "role", "is_active", "is_staff", "is_superuser", "created_at", "password"]
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
            raise serializers.ValidationError("Email or password is incorrect.")
        attrs["user"] = user
        return attrs


class ProgramSerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source="tenant.name", read_only=True)

    class Meta:
        model = Program
        fields = "__all__"
        read_only_fields = ["tenant", "created_by", "created_at"]


class ProgramActivitySerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    owner_name = serializers.CharField(source="owner.full_name", read_only=True)

    class Meta:
        model = ProgramActivity
        fields = "__all__"
        read_only_fields = ["tenant", "created_by", "created_at", "updated_at"]

    def validate(self, attrs):
        program = attrs.get("program") or self.instance.program
        owner = attrs.get("owner")
        progress = attrs.get("progress")
        if not self.context["request"].user.is_superuser and program.tenant_id != self.context["request"].user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        if owner and owner.tenant_id != program.tenant_id:
            raise serializers.ValidationError({"owner": "Owner belongs to another tenant"})
        if progress is not None and progress > 100:
            raise serializers.ValidationError({"progress": "Progress must be between 0 and 100"})
        return attrs


class ActivityDependencySerializer(serializers.ModelSerializer):
    predecessor_name = serializers.CharField(source="predecessor.name", read_only=True)
    successor_name = serializers.CharField(source="successor.name", read_only=True)

    class Meta:
        model = ActivityDependency
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]

    def validate(self, attrs):
        predecessor = attrs.get("predecessor") or self.instance.predecessor
        successor = attrs.get("successor") or self.instance.successor
        user = self.context["request"].user
        if predecessor == successor or predecessor.tenant_id != successor.tenant_id or predecessor.program_id != successor.program_id:
            raise serializers.ValidationError("Dependencies must link different activities in the same tenant and program")
        if not user.is_superuser and predecessor.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Activities belong to another tenant")
        seen, stack = set(), [successor]
        while stack:
            activity = stack.pop()
            if activity.id == predecessor.id:
                raise serializers.ValidationError("This dependency would create a cycle")
            if activity.id in seen:
                continue
            seen.add(activity.id)
            stack.extend(dependency.successor for dependency in activity.successor_dependencies.all())
        return attrs


class HouseholdSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    registration_reference = serializers.SerializerMethodField()

    class Meta:
        model = Household
        fields = "__all__"
        read_only_fields = ["tenant", "created_by", "created_at", "client_generated_id"]

    def validate_program(self, program):
        if program.tenant_id != self.context["request"].user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        return program

    def get_registration_reference(self, household):
        return household.client_generated_id or f"HH-{str(household.id).split('-')[0].upper()}"


class BeneficiarySerializer(serializers.ModelSerializer):
    number = serializers.CharField(write_only=True, required=False)
    national_id = serializers.CharField(write_only=True, required=False)
    national_id_hash = serializers.CharField(write_only=True, required=False)
    phone_last4 = serializers.CharField(write_only=True, required=False)
    household_reference = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="household.program.name", read_only=True)
    national_id_reference = serializers.SerializerMethodField()
    masked_phone = serializers.SerializerMethodField()

    class Meta:
        model = Beneficiary
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]

    def to_internal_value(self, data):
        normalized_data = data.copy()
        if not normalized_data.get("number") and normalized_data.get("national_id"):
            normalized_data["number"] = normalized_data["national_id"]
        return super().to_internal_value(normalized_data)

    def validate_household(self, household):
        if household.tenant_id != self.context["request"].user.tenant_id:
            raise serializers.ValidationError("Household belongs to another tenant")
        return household

    def validate(self, attrs):
        request = self.context["request"]
        protected_fields = {"verification_status", "status"}
        if request.user.role == User.Role.FIELD_OFFICER and protected_fields.intersection(attrs):
            raise serializers.ValidationError("Field officers cannot set verification or workflow status.")
        attrs.pop("national_id_hash", None)
        attrs.pop("phone_last4", None)
        submitted_national_id = attrs.pop("national_id", None) or attrs.get("number")
        if submitted_national_id:
            normalized_national_id = normalize_national_id(submitted_national_id)
            attrs["number"] = national_id_reference(normalized_national_id)
            attrs["national_id_hash"] = national_id_digest(normalized_national_id)
        elif not self.instance:
            raise serializers.ValidationError({"national_id": "National ID is required."})
        if "phone_number" in attrs:
            normalized_phone = normalize_phone_number(attrs["phone_number"])
            attrs["phone_number"] = normalized_phone
            attrs["phone_last4"] = normalized_phone[-4:] if normalized_phone else ""
        return attrs

    def get_household_reference(self, beneficiary):
        household = beneficiary.household
        return household.client_generated_id or f"HH-{str(household.id).split('-')[0].upper()}"

    def get_national_id_reference(self, beneficiary):
        return f"•••• {beneficiary.number[-4:]}" if beneficiary.number else "Not recorded"

    def get_masked_phone(self, beneficiary):
        phone_number = beneficiary.phone_number or beneficiary.phone_last4
        return f"**** {phone_number[-4:]}" if phone_number else "Not recorded"


class EnrollmentSerializer(serializers.ModelSerializer):
    beneficiary_name = serializers.CharField(source="beneficiary.full_name", read_only=True)
    beneficiary_number = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="program.name", read_only=True)

    class Meta:
        model = Enrollment
        fields = "__all__"
        read_only_fields = ["approved_by", "approved_at", "enrolled_at"]

    def get_beneficiary_number(self, enrollment):
        return f"•••• {enrollment.beneficiary.number[-4:]}" if enrollment.beneficiary.number else "Not recorded"

    def validate(self, attrs):
        beneficiary = attrs.get("beneficiary") or self.instance.beneficiary
        program = attrs.get("program") or self.instance.program
        user = self.context["request"].user
        if beneficiary.household.tenant_id != program.tenant_id:
            raise serializers.ValidationError("Beneficiary and program must belong to the same tenant")
        if not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        return attrs

    def update(self, instance, validated_data):
        status = validated_data.get("status")
        eligibility_status = validated_data.get("eligibility_status", instance.eligibility_status)
        if status == Enrollment.Status.APPROVED and eligibility_status != Enrollment.EligibilityStatus.ELIGIBLE:
            raise serializers.ValidationError({"status": "Only an eligible enrollment can be approved."})
        if status in {Enrollment.Status.APPROVED, Enrollment.Status.REJECTED}:
            instance.approved_by = self.context["request"].user
            instance.approved_at = timezone.now()
        return super().update(instance, validated_data)


class PaymentChannelConfigSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)

    class Meta:
        model = PaymentChannelConfig
        fields = "__all__"


class PaymentInstructionSerializer(serializers.ModelSerializer):
    beneficiary_name = serializers.CharField(source="beneficiary.full_name", read_only=True)
    program_name = serializers.CharField(source="enrollment.program.name", read_only=True)
    channel_name = serializers.CharField(source="channel_config.provider_name", read_only=True)

    class Meta:
        model = PaymentInstruction
        fields = "__all__"
        read_only_fields = ["created_by", "created_at", "status", "provider_reference", "idempotency_key"]

    def validate(self, attrs):
        enrollment = attrs.get("enrollment") or self.instance.enrollment
        beneficiary = attrs.get("beneficiary") or self.instance.beneficiary
        channel_config = attrs.get("channel_config") or self.instance.channel_config
        batch = attrs.get("batch", self.instance.batch if self.instance else None)
        if enrollment.status != Enrollment.Status.APPROVED:
            raise serializers.ValidationError("Payment instruction requires an approved enrollment")
        if beneficiary.pk != enrollment.beneficiary.pk:
            raise serializers.ValidationError("Beneficiary must match enrollment")
        if channel_config.program.pk != enrollment.program.pk:
            raise serializers.ValidationError("Channel must belong to the enrollment program")
        if batch and (batch.tenant_id != enrollment.program.tenant_id or batch.program_id != enrollment.program_id):
            raise serializers.ValidationError({"batch": "Payment batch must belong to the enrollment program"})
        if not self.context["request"].user.is_superuser and enrollment.program.tenant_id != self.context["request"].user.tenant_id:
            raise serializers.ValidationError("Enrollment belongs to another tenant")
        if not enrollment.program.workflow_config.get("payment_enabled", True):
            raise serializers.ValidationError("Payments are disabled for this program")
        if PaymentInstruction.objects.filter(enrollment=enrollment).exists():
            raise serializers.ValidationError("A payment instruction already exists for this enrollment; use its controlled retry action instead")
        return attrs


class PaymentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentEvent
        fields = "__all__"


class PaymentBatchSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)

    class Meta:
        model = PaymentBatch
        fields = "__all__"
        read_only_fields = ["tenant", "created_by", "created_at", "idempotency_key"]


class ComplaintSerializer(serializers.ModelSerializer):
    beneficiary_name = serializers.CharField(source="beneficiary.full_name", read_only=True)
    program_name = serializers.CharField(source="beneficiary.household.program.name", read_only=True)
    household_reference = serializers.SerializerMethodField()
    assigned_to_name = serializers.CharField(source="assigned_to.full_name", read_only=True)

    class Meta:
        model = Complaint
        fields = "__all__"
        read_only_fields = ["created_by", "created_at", "resolved_at"]

    def validate_beneficiary(self, beneficiary):
        if beneficiary.household.tenant_id != self.context["request"].user.tenant_id:
            raise serializers.ValidationError("Beneficiary belongs to another tenant")
        return beneficiary

    def validate(self, attrs):
        beneficiary = attrs.get("beneficiary") or self.instance.beneficiary
        assigned_to = attrs.get("assigned_to")
        if assigned_to and assigned_to.tenant_id != beneficiary.household.tenant_id:
            raise serializers.ValidationError({"assigned_to": "Assignee belongs to another tenant."})
        return attrs

    def update(self, instance, validated_data):
        if validated_data.get("status") in {Complaint.Status.RESOLVED, Complaint.Status.CLOSED}:
            instance.resolved_at = timezone.now()
        return super().update(instance, validated_data)

    def get_household_reference(self, complaint):
        household = complaint.beneficiary.household
        return household.client_generated_id or f"HH-{str(household.id).split('-')[0].upper()}"


class BudgetSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
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
    program_name = serializers.CharField(source="program.name", read_only=True)

    class Meta:
        model = AISignal
        fields = "__all__"
        read_only_fields = ["tenant", "reviewed_by", "reviewed_at", "created_at"]


class ReconciliationItemSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    instruction_reference = serializers.CharField(source="instruction.provider_reference", read_only=True)

    class Meta:
        model = ReconciliationItem
        fields = "__all__"
        read_only_fields = ["tenant", "resolved_by", "resolved_at", "created_at"]


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
