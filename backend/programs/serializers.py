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



# Validates program setup and returns its enabled assistance configuration.
class ProgramSerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source="tenant.name", read_only=True)

    class Meta:
        model = Program
        fields = "__all__"
        read_only_fields = [
            "tenant", "created_by", "created_at", "status", "status_reason",
            "status_changed_at", "status_changed_by",
        ]
        extra_kwargs = {
            "transfer_amount": {"required": False, "allow_null": True},
            "payment_cycle": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        cash_enabled = attrs.get("cash_enabled", self.instance.cash_enabled if self.instance else True)
        nfi_enabled = attrs.get("nfi_enabled", self.instance.nfi_enabled if self.instance else False)
        medical_enabled = attrs.get("medical_enabled", self.instance.medical_enabled if self.instance else False)
        payment_cycle = attrs.get("payment_cycle", self.instance.payment_cycle if self.instance else "")
        if not any([cash_enabled, nfi_enabled, medical_enabled]):
            raise serializers.ValidationError(
                "Enable at least one implemented assistance modality: Payment/Cash, NFI, or Medical Assistance."
            )
        if self.instance:
            if self.instance.cash_enabled and not cash_enabled and CashEntitlement.objects.filter(program=self.instance).exists():
                raise serializers.ValidationError({"cash_enabled": "Cash cannot be disabled after cash entitlement history exists."})
            if self.instance.nfi_enabled and not nfi_enabled and NFIEntitlement.objects.filter(program=self.instance).exists():
                raise serializers.ValidationError({"nfi_enabled": "NFI cannot be disabled after NFI entitlement history exists."})
            if self.instance.medical_enabled and not medical_enabled:
                from medical.models import MedicalBatch, MedicalEntitlement
                if MedicalEntitlement.objects.filter(program=self.instance).exists() or MedicalBatch.objects.filter(program=self.instance).exists():
                    raise serializers.ValidationError({"medical_enabled": "Medical Assistance cannot be disabled after medical inventory or entitlement history exists."})
        if cash_enabled:
            if not payment_cycle:
                raise serializers.ValidationError({"payment_cycle": "Payment cycle is required when Payment/Cash is enabled."})
            # Household payment amounts belong to CashEntitlement. Keep the
            # legacy program field nullable for existing records only.
            attrs["transfer_amount"] = None
        else:
            # Cash fields are deliberately cleared when Cash is disabled, so an
            # NFI-only program cannot carry a misleading transfer configuration.
            attrs["transfer_amount"] = None
            attrs["payment_cycle"] = ""
        return attrs


# Validates activity dates, ownership, and program lifecycle rules.
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


# Returns the cash program budget with calculated financial totals.
class BudgetSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    used_amount = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    committed_amount = serializers.SerializerMethodField()
    successful_funding = serializers.SerializerMethodField()
    successful_fees = serializers.SerializerMethodField()
    class Meta:
        model = Budget
        fields = "__all__"
        read_only_fields = ["actual_total", "created_at"]

    def financials(self, budget):
        committed = CashEntitlement.objects.filter(program=budget.program, status__in=[CashEntitlement.Status.ACTIVE, CashEntitlement.Status.PAID]).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        successful = PaymentInstruction.objects.filter(cash_entitlement__program=budget.program, status=PaymentInstruction.Status.SUCCESS)
        funding = successful.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        fees = successful.aggregate(total=Sum("applied_fee"))["total"] or Decimal("0")
        return committed, funding, fees

    def get_committed_amount(self, budget):
        return self.financials(budget)[0]

    def get_successful_funding(self, budget):
        return self.financials(budget)[1]

    def get_successful_fees(self, budget):
        return self.financials(budget)[2]

    def get_used_amount(self, budget):
        committed, _funding, fees = self.financials(budget)
        return committed + fees

    def get_remaining_amount(self, budget):
        return budget.planned_total - self.get_used_amount(budget)

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
        user = self.context["request"].user
        if not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        if not program.cash_enabled:
            raise serializers.ValidationError("A payment budget requires a payment-enabled program")
        return program

    def validate(self, attrs):
        program = attrs.get("program") or getattr(self.instance, "program", None)
        currency = attrs.get("currency", getattr(self.instance, "currency", None))
        planned_total = attrs.get("planned_total", getattr(self.instance, "planned_total", Decimal("0")))
        if program and currency != program.currency:
            raise serializers.ValidationError({"currency": "Budget currency must match the program currency"})
        if program:
            source = self.instance or type("BudgetProgram", (), {"program": program})()
            committed, _funding, fees = self.financials(source)
            if planned_total < committed + fees:
                raise serializers.ValidationError({"planned_total": "Budget amount cannot be below current commitments and successful fees"})
        attrs.pop("actual_total", None)
        return attrs
