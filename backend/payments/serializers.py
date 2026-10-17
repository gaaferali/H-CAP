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



# Validates the simulated payment channel configured for a cash program.
class PaymentChannelConfigSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)

    class Meta:
        model = PaymentChannelConfig
        fields = "__all__"

    def validate(self, attrs):
        program = attrs.get("program") or self.instance.program
        currency = attrs.get("currency", self.instance.currency if self.instance else None)
        if not program.cash_enabled:
            raise serializers.ValidationError({"program": "Payment channels require a payment-enabled program"})
        if currency != program.currency:
            raise serializers.ValidationError({"currency": "Channel currency must match the program currency"})
        user = self.context["request"].user
        if not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        return attrs


# Validates payment creation and returns readable household and program details.
class PaymentInstructionSerializer(serializers.ModelSerializer):
    beneficiary_name = serializers.SerializerMethodField()
    program_id = serializers.SerializerMethodField()
    program_name = serializers.SerializerMethodField()
    household_reference = serializers.SerializerMethodField()
    household_member_count = serializers.SerializerMethodField()
    household_size = serializers.SerializerMethodField()
    household_members = serializers.SerializerMethodField()
    household = serializers.PrimaryKeyRelatedField(
        queryset=Household.objects.all(),
        write_only=True,
        required=False,
    )
    channel_name = serializers.CharField(source="channel_config.provider_name", read_only=True)
    fee_amount = serializers.DecimalField(source="applied_fee", max_digits=18, decimal_places=2, read_only=True)
    total_financial_impact = serializers.SerializerMethodField()

    class Meta:
        model = PaymentInstruction
        fields = "__all__"
        read_only_fields = ["created_by", "created_at", "status", "provider_reference", "idempotency_key", "applied_fee"]
        extra_kwargs = {"amount": {"required": False}, "currency": {"required": False}}

    def validate(self, attrs):
        entitlement = attrs.get("cash_entitlement")
        if self.instance is not None:
            entitlement = self.instance.cash_entitlement
        household = attrs.get("household") or (
            entitlement.household if entitlement else None
        )
        if self.instance is None and not entitlement and not household:
            raise serializers.ValidationError(
                {"household": "An approved household is required"}
            )
        if attrs.get("enrollment"):
            raise serializers.ValidationError({"enrollment": "New instructions must use a household cash entitlement, not a legacy enrollment"})

        if entitlement and household and entitlement.household_id != household.id:
            raise serializers.ValidationError(
                {"cash_entitlement": "Cash entitlement must belong to the selected household"}
            )
        if not household:
            raise serializers.ValidationError(
                {"household": "Payment instructions must be assigned to a household"}
            )
        program = entitlement.program if entitlement else household.program
        if household.program_id != program.id or household.tenant_id != program.tenant_id:
            raise serializers.ValidationError(
                {"household": "Household must belong to the payment program and tenant"}
            )
        channel_config = attrs.get("channel_config", getattr(self.instance, "channel_config", None))
        batch = attrs.get("batch", getattr(self.instance, "batch", None))
        beneficiary = attrs.get("beneficiary")
        if entitlement and beneficiary and beneficiary.pk != entitlement.beneficiary_id:
            raise serializers.ValidationError({"beneficiary": "Beneficiary must match the household cash entitlement"})
        if entitlement and entitlement.status != CashEntitlement.Status.ACTIVE:
            raise serializers.ValidationError({"cash_entitlement": "Only active cash entitlements can be paid"})
        if not program.cash_enabled:
            raise serializers.ValidationError({"program": "Cash assistance is disabled for this program"})
        if not program.workflow_config.get("payment_enabled", True):
            raise serializers.ValidationError("Payments are disabled for this program")
        if not HouseholdEligibility.objects.filter(
            household=household,
            program=program,
            status=HouseholdEligibility.Status.ELIGIBLE,
        ).exists():
            raise serializers.ValidationError(
                {"household": "Household must have eligible status"}
            )
        approved_enrollment = approved_household_modality(
            household,
            program,
            [Enrollment.AssistanceModality.CASH, Enrollment.AssistanceModality.CASH_NFI],
        )
        if not approved_enrollment:
            raise serializers.ValidationError({"household": "Payment instruction requires an approved household cash enrollment"})
        if not channel_config or channel_config.program_id != program.id or not channel_config.is_active:
            raise serializers.ValidationError({"channel_config": "An active payment channel must belong to the program"})
        submitted_amount = attrs.get("amount") if not entitlement else self.initial_data.get("amount")
        submitted_currency = attrs.get("currency") if not entitlement else self.initial_data.get("currency")
        if entitlement:
            if submitted_amount not in {None, ""} and Decimal(str(submitted_amount)) != entitlement.amount:
                raise serializers.ValidationError({"amount": "Payment amount must match the household cash entitlement"})
            if submitted_currency not in {None, ""} and submitted_currency != entitlement.currency:
                raise serializers.ValidationError({"currency": "Payment currency must match the household cash entitlement"})
            if entitlement.currency != program.currency or entitlement.currency != channel_config.currency:
                raise serializers.ValidationError({"currency": "Payment, program, and channel currencies must match"})
        else:
            if submitted_amount in {None, ""}:
                raise serializers.ValidationError({"amount": "Enter the payment amount"})
            if Decimal(str(submitted_amount)) <= 0:
                raise serializers.ValidationError({"amount": "Payment amount must be greater than zero"})
            if submitted_currency != program.currency or submitted_currency != channel_config.currency:
                raise serializers.ValidationError({"currency": "Payment, program, and channel currencies must match"})
        if batch and (batch.tenant_id != program.tenant_id or batch.program_id != program.id):
            raise serializers.ValidationError({"batch": "Payment batch must belong to the program and tenant"})
        user = self.context["request"].user
        if not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Household belongs to another tenant")
        if entitlement and PaymentInstruction.objects.filter(cash_entitlement=entitlement).exclude(pk=getattr(self.instance, "pk", None)).exists():
            raise serializers.ValidationError("A payment instruction already exists for this cash entitlement; use its controlled retry action instead")
        if self.instance is None and not attrs.get("household"):
            attrs["beneficiary"] = entitlement.beneficiary if entitlement else None
        return attrs

    def create(self, validated_data):
        validated_data.pop("household", None)
        return super().create(validated_data)

    def get_beneficiary_name(self, instruction):
        return instruction.beneficiary.full_name if instruction.beneficiary else None

    def get_program_name(self, instruction):
        if instruction.cash_entitlement_id:
            return instruction.cash_entitlement.program.name
        return instruction.enrollment.program.name if instruction.enrollment_id else None

    def get_program_id(self, instruction):
        if instruction.cash_entitlement_id:
            return str(instruction.cash_entitlement.program_id)
        return str(instruction.enrollment.program_id) if instruction.enrollment_id else None

    def get_household(self, instruction):
        if instruction.cash_entitlement_id:
            return instruction.cash_entitlement.household
        if instruction.enrollment_id and instruction.enrollment.beneficiary_id:
            return instruction.enrollment.beneficiary.household
        return None

    def get_household_reference(self, instruction):
        return household_reference(self.get_household(instruction))

    def get_household_member_count(self, instruction):
        return household_member_count(self.get_household(instruction))

    def get_household_size(self, instruction):
        household = self.get_household(instruction)
        return household.household_size if household else None

    def get_household_members(self, instruction):
        return household_member_details(self.get_household(instruction))

    def get_total_financial_impact(self, instruction):
        return instruction.amount + instruction.applied_fee


# Returns the readable append-only history for a payment instruction.
class PaymentEventSerializer(serializers.ModelSerializer):
    action = serializers.CharField(source="event_type", read_only=True)
    actor_name = serializers.CharField(source="recorded_by.full_name", read_only=True)
    household_reference = serializers.SerializerMethodField()
    household_size = serializers.SerializerMethodField()
    household_members = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="instruction.cash_entitlement.program.name", read_only=True)
    program_id = serializers.SerializerMethodField()
    payment_amount = serializers.DecimalField(source="instruction.amount", max_digits=18, decimal_places=2, read_only=True)
    fee_amount = serializers.DecimalField(source="instruction.applied_fee", max_digits=18, decimal_places=2, read_only=True)
    total_financial_impact = serializers.SerializerMethodField()
    payment_reference = serializers.CharField(source="instruction.provider_reference", read_only=True)

    class Meta:
        model = PaymentEvent
        fields = "__all__"

    def get_household(self, event):
        return event.instruction.cash_entitlement.household if event.instruction.cash_entitlement_id else None

    def get_program_id(self, event):
        instruction = event.instruction
        if instruction.cash_entitlement_id:
            return str(instruction.cash_entitlement.program_id)
        return str(instruction.enrollment.program_id) if instruction.enrollment_id else None

    def get_household_reference(self, event):
        return household_reference(self.get_household(event))

    def get_household_size(self, event):
        household = self.get_household(event)
        return household.household_size if household else None

    def get_household_members(self, event):
        return household_member_details(self.get_household(event))

    def get_total_financial_impact(self, event):
        return event.instruction.amount + event.instruction.applied_fee


class PaymentBatchSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)

    class Meta:
        model = PaymentBatch
        fields = "__all__"
        read_only_fields = ["tenant", "created_by", "created_at", "idempotency_key"]


# Returns finance reconciliation issues and their review status.
class ReconciliationItemSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    instruction_reference = serializers.CharField(source="instruction.provider_reference", read_only=True)

    class Meta:
        model = ReconciliationItem
        fields = "__all__"
        read_only_fields = ["tenant", "resolved_by", "resolved_at", "created_at"]
