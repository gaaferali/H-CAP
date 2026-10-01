from django.contrib.auth import authenticate
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.db.models import Sum
from rest_framework import serializers
import re
from decimal import Decimal

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
    ProgramActivity,
    Program,
    ReconciliationItem,
    ReviewTask,
    Tenant,
    User,
    HouseholdEligibility, HouseholdEnrollment, CashEntitlement, Warehouse, NFIItem,
    NFIEntitlement, StockMovement, DistributionEvent, DistributionAllocation, DistributionIssue,
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
        extra_kwargs = {
            "transfer_amount": {"required": False, "allow_null": True},
            "payment_cycle": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        cash_enabled = attrs.get("cash_enabled", self.instance.cash_enabled if self.instance else True)
        transfer_amount = attrs.get("transfer_amount", self.instance.transfer_amount if self.instance else None)
        payment_cycle = attrs.get("payment_cycle", self.instance.payment_cycle if self.instance else "")
        if cash_enabled:
            if transfer_amount is None:
                raise serializers.ValidationError({"transfer_amount": "Transfer amount is required when Payment/Cash is enabled."})
            if transfer_amount < Decimal("0.01"):
                raise serializers.ValidationError({"transfer_amount": "Transfer amount must be at least 0.01 when Payment/Cash is enabled."})
            if not payment_cycle:
                raise serializers.ValidationError({"payment_cycle": "Payment cycle is required when Payment/Cash is enabled."})
        else:
            # Cash fields are deliberately cleared when Cash is disabled, so an
            # NFI-only program cannot carry a misleading transfer configuration.
            attrs["transfer_amount"] = None
            attrs["payment_cycle"] = ""
        return attrs


class HouseholdEligibilitySerializer(serializers.ModelSerializer):
    beneficiaries = serializers.SerializerMethodField()
    class Meta:
        model = HouseholdEligibility
        fields = "__all__"
        read_only_fields = ["decided_by", "decided_at", "created_at"]
    def get_beneficiaries(self, obj):
        return list(obj.household.beneficiaries.values("id", "full_name", "number", "status"))

    def validate(self, attrs):
        household = attrs.get("household") or self.instance.household
        program = attrs.get("program") or self.instance.program
        user = self.context["request"].user
        if household.program_id != program.id:
            raise serializers.ValidationError({"program": "Program must match the household program"})
        if not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        return attrs


class HouseholdEnrollmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = HouseholdEnrollment
        fields = "__all__"
        read_only_fields = ["decided_by", "decided_at", "created_at"]

    def validate(self, attrs):
        household = attrs.get("household") or self.instance.household
        program = attrs.get("program") or self.instance.program
        user = self.context["request"].user
        if household.program_id != program.id:
            raise serializers.ValidationError({"program": "Program must match the household program"})
        if not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        return attrs


class CashEntitlementSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashEntitlement
        fields = "__all__"
        read_only_fields = ["created_by", "created_at", "updated_at"]

    def validate(self, attrs):
        beneficiary = attrs.get("beneficiary") or self.instance.beneficiary
        program = attrs.get("program") or self.instance.program
        user = self.context["request"].user
        if beneficiary.household.program_id != program.id:
            raise serializers.ValidationError({"program": "Program must match the beneficiary household program"})
        if not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        if not program.cash_enabled:
            raise serializers.ValidationError({"program": "Cash assistance is disabled for this program"})
        if not Enrollment.objects.filter(beneficiary=beneficiary, program=program, status=Enrollment.Status.APPROVED, assistance_modality__in=[Enrollment.AssistanceModality.CASH, Enrollment.AssistanceModality.CASH_NFI]).exists():
            raise serializers.ValidationError({"beneficiary": "Beneficiary is not approved for cash assistance"})
        return attrs


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]


class NFIItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = NFIItem
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]


class NFIEntitlementSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    beneficiary_name = serializers.CharField(source="beneficiary.full_name", read_only=True)
    beneficiary_number = serializers.CharField(source="beneficiary.number", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = NFIEntitlement
        fields = "__all__"
        read_only_fields = ["created_by", "created_at", "updated_at"]

    def validate(self, attrs):
        beneficiary = attrs.get("beneficiary") or self.instance.beneficiary
        program = attrs.get("program") or self.instance.program
        item = attrs.get("item") or self.instance.item
        warehouse = attrs.get("warehouse", self.instance.warehouse if self.instance else None)
        user = self.context["request"].user
        if beneficiary.household.program_id != program.id:
            raise serializers.ValidationError({"program": "Program must match the beneficiary household program"})
        if not warehouse:
            raise serializers.ValidationError({"warehouse": "Warehouse is required for NFI entitlement"})
        if item.program_id != program.id or warehouse.program_id != program.id or item.warehouse_id != warehouse.id:
            raise serializers.ValidationError("Item and warehouse must belong to the selected program")
        if not program.nfi_enabled:
            raise serializers.ValidationError({"program": "NFI assistance is disabled for this program"})
        if attrs.get("quantity", self.instance.quantity if self.instance else 0) < 1:
            raise serializers.ValidationError({"quantity": "NFI entitlement quantity must be greater than zero"})
        if not item.active or item.available_quantity < 1:
            raise serializers.ValidationError({"item": "Item is inactive or has no available stock"})
        if not Enrollment.objects.filter(beneficiary=beneficiary, program=program, status=Enrollment.Status.APPROVED, assistance_modality__in=[Enrollment.AssistanceModality.NFI, Enrollment.AssistanceModality.CASH_NFI]).exists():
            raise serializers.ValidationError({"beneficiary": "Beneficiary is not approved for NFI assistance"})
        if not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        return attrs


class StockMovementSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockMovement
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]


class DistributionEventSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = DistributionEvent
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]

    def validate(self, attrs):
        program = attrs.get("program") or self.instance.program
        warehouse = attrs.get("warehouse", self.instance.warehouse if self.instance else None)
        if not program.nfi_enabled:
            raise serializers.ValidationError({"program": "NFI assistance is disabled for this program"})
        if not warehouse or warehouse.program_id != program.id:
            raise serializers.ValidationError({"warehouse": "Warehouse must belong to the selected NFI program"})
        user = self.context["request"].user
        if not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        return attrs


class DistributionAllocationSerializer(serializers.ModelSerializer):
    beneficiary_name = serializers.CharField(source="beneficiary.full_name", read_only=True)
    beneficiary_number = serializers.CharField(source="beneficiary.number", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    event_date = serializers.DateField(source="event.event_date", read_only=True)
    event_location = serializers.CharField(source="event.location", read_only=True)

    class Meta:
        model = DistributionAllocation
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]

    def validate(self, attrs):
        entitlement = attrs.get("entitlement") or self.instance.entitlement
        event = attrs.get("event") or self.instance.event
        item = attrs.get("item") or self.instance.item
        beneficiary = attrs.get("beneficiary") or self.instance.beneficiary
        planned = attrs.get("planned_quantity", self.instance.planned_quantity if self.instance else 0)
        allocated = attrs.get("allocated_quantity", self.instance.allocated_quantity if self.instance else 0)
        if entitlement.beneficiary_id != beneficiary.id or entitlement.item_id != item.id:
            raise serializers.ValidationError("Beneficiary and item must match the NFI entitlement")
        if event.program_id != entitlement.program_id or event.warehouse_id != entitlement.warehouse_id:
            raise serializers.ValidationError("Distribution event must match the entitlement program and warehouse")
        already_allocated = DistributionAllocation.objects.filter(entitlement=entitlement).exclude(pk=getattr(self.instance, "pk", None)).aggregate(total=Sum("allocated_quantity"))["total"] or 0
        if planned != allocated or allocated < 1 or already_allocated + allocated > entitlement.quantity:
            raise serializers.ValidationError("Allocation must use the exact remaining entitlement quantity")
        return attrs


class DistributionIssueSerializer(serializers.ModelSerializer):
    beneficiary_name = serializers.CharField(source="beneficiary.full_name", read_only=True)
    beneficiary_number = serializers.CharField(source="beneficiary.number", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    event_date = serializers.DateField(source="event.event_date", read_only=True)
    event_location = serializers.CharField(source="event.location", read_only=True)

    class Meta:
        model = DistributionIssue
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]

    def validate(self, attrs):
        entitlement = attrs.get("entitlement") or self.instance.entitlement
        event = attrs.get("event") or self.instance.event
        beneficiary = attrs.get("beneficiary") or self.instance.beneficiary
        item = attrs.get("item") or self.instance.item
        planned = attrs.get("planned_quantity", self.instance.planned_quantity if self.instance else None)
        actual = attrs.get("actual_quantity", self.instance.actual_quantity if self.instance else 0)
        if entitlement.beneficiary_id != beneficiary.id or entitlement.item_id != item.id:
            raise serializers.ValidationError("Beneficiary and item must match the entitlement")
        if event.program_id != entitlement.program_id or (event.warehouse_id and event.warehouse_id != entitlement.warehouse_id):
            raise serializers.ValidationError("Distribution event must match the entitlement program and warehouse")
        if not event.warehouse_id:
            raise serializers.ValidationError({"event": "Distribution event must have a warehouse"})
        allocation = DistributionAllocation.objects.filter(entitlement=entitlement, event=event, beneficiary=beneficiary, item=item).first()
        if not allocation:
            raise serializers.ValidationError("Delivery review requires an allocation from this distribution event")
        already_delivered = DistributionIssue.objects.filter(entitlement=entitlement, event=event).exclude(pk=getattr(self.instance, "pk", None)).aggregate(total=Sum("actual_quantity"))["total"] or 0
        if planned != allocation.allocated_quantity or planned < 1 or actual > planned or already_delivered + actual > allocation.allocated_quantity:
            raise serializers.ValidationError("Actual delivery cannot exceed the planned entitlement quantity")
        user = self.context["request"].user
        if not user.is_superuser and event.program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Distribution event belongs to another tenant")
        return attrs


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
        submitted_modality = self.initial_data.get("assistance_modality") if self.initial_data is not None else None
        if submitted_modality:
            modality = attrs["assistance_modality"]
        elif self.instance is not None:
            modality = self.instance.assistance_modality
        elif program.nfi_enabled and not program.cash_enabled:
            # Eligibility/enrollment starts before the reviewer chooses a
            # modality. Infer the only valid modality for NFI-only programs
            # instead of defaulting to CASH and rejecting the enrollment.
            modality = Enrollment.AssistanceModality.NFI
        else:
            modality = Enrollment.AssistanceModality.CASH
        attrs["assistance_modality"] = modality
        if modality in {Enrollment.AssistanceModality.CASH, Enrollment.AssistanceModality.CASH_NFI} and not program.cash_enabled:
            raise serializers.ValidationError({"assistance_modality": "Payment is disabled for this program"})
        if modality in {Enrollment.AssistanceModality.NFI, Enrollment.AssistanceModality.CASH_NFI} and not program.nfi_enabled:
            raise serializers.ValidationError({"assistance_modality": "NFI is disabled for this program"})
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
        if not enrollment.program.cash_enabled:
            raise serializers.ValidationError({"enrollment": "Cash assistance is disabled for this program"})
        if enrollment.assistance_modality not in {Enrollment.AssistanceModality.CASH, Enrollment.AssistanceModality.CASH_NFI}:
            raise serializers.ValidationError({"enrollment": "Beneficiary is not approved for cash assistance"})
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
    ai_analysis_available = serializers.SerializerMethodField()

    class Meta:
        model = Complaint
        fields = "__all__"
        read_only_fields = ["created_by", "created_at", "resolved_at"]

    def to_internal_value(self, data):
        # Older import clients used this descriptive label before complaint
        # categories were constrained to the current operational choices.
        mutable = data.copy() if hasattr(data, "copy") else dict(data)
        if str(mutable.get("category", "")).strip().lower() == "information request":
            mutable["category"] = Complaint.Category.ACCESS
        return super().to_internal_value(mutable)

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

    def get_ai_analysis_available(self, complaint):
        return ComplaintAIAnalysis.objects.filter(complaint=complaint).exists()


class ComplaintAIAnalysisSerializer(serializers.ModelSerializer):
    complaint_id = serializers.UUIDField(source="complaint.id", read_only=True)
    human_review_required = serializers.SerializerMethodField()

    class Meta:
        model = ComplaintAIAnalysis
        fields = ["complaint_id", "category", "severity", "summary", "possible_causes", "recommended_actions", "evidence", "confidence", "requires_escalation", "model_version", "human_review_required", "review_decision", "reviewer_note", "reviewed_by", "reviewed_at", "created_at", "updated_at"]
        read_only_fields = fields

    def get_human_review_required(self, analysis):
        return True

    def to_representation(self, instance):
        data = super().to_representation(instance)
        evidence = data.get("evidence") or {}
        if not evidence.get("payment_context"):
            evidence["payment_context"] = {"available": False, "message": "No linked payment instruction or payment evidence was available."}
        data["evidence"] = evidence
        return data


class ComplaintAIAnalysisSerializer(serializers.ModelSerializer):
    complaint_id = serializers.UUIDField(source="complaint.id", read_only=True)
    human_review_required = serializers.SerializerMethodField()

    class Meta:
        model = ComplaintAIAnalysis
        fields = [
            "complaint_id", "category", "severity", "summary", "possible_causes",
            "recommended_actions", "evidence", "confidence", "requires_escalation",
            "model_version", "human_review_required", "review_decision", "reviewer_note",
            "reviewed_by", "reviewed_at", "created_at", "updated_at",
        ]
        read_only_fields = fields

    def get_human_review_required(self, analysis):
        return True

    def to_representation(self, instance):
        data = super().to_representation(instance)
        evidence = data.get("evidence") or {}
        if not evidence.get("payment_context"):
            evidence["payment_context"] = {
                "available": False,
                "message": "No linked payment instruction or payment evidence was available.",
            }
        data["evidence"] = evidence
        return data


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
