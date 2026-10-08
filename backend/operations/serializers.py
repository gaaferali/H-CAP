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



class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]


class NFIItemSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    committed_quantity = serializers.SerializerMethodField()
    delivered_quantity = serializers.SerializerMethodField()

    class Meta:
        model = NFIItem
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]

    def get_committed_quantity(self, item):
        return item.entitlements.filter(status__in=[NFIEntitlement.Status.ACTIVE, NFIEntitlement.Status.DISTRIBUTED]).aggregate(total=Sum("quantity"))["total"] or 0

    def get_delivered_quantity(self, item):
        return item.issues.aggregate(total=Sum("actual_quantity"))["total"] or 0


class NFIEntitlementSerializer(serializers.ModelSerializer):
    household_reference = serializers.SerializerMethodField()
    household_member_count = serializers.SerializerMethodField()
    household_size = serializers.SerializerMethodField()
    household_members = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="program.name", read_only=True)
    beneficiary_name = serializers.CharField(source="beneficiary.full_name", read_only=True)
    national_id_reference = serializers.SerializerMethodField()
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True, allow_null=True)

    class Meta:
        model = NFIEntitlement
        fields = "__all__"
        read_only_fields = ["created_by", "created_at", "updated_at"]

    def get_national_id_reference(self, entitlement):
        return safe_beneficiary_reference(entitlement.beneficiary) if entitlement.beneficiary else "Not recorded"

    def validate(self, attrs):
        beneficiary = attrs.get("beneficiary") or getattr(self.instance, "beneficiary", None)
        household = attrs.get("household") or getattr(self.instance, "household", None) or getattr(beneficiary, "household", None)
        program = attrs.get("program") or getattr(self.instance, "program", None)
        item = attrs.get("item") or self.instance.item
        warehouse = attrs.get("warehouse", self.instance.warehouse if self.instance else None)
        user = self.context["request"].user
        if not household or household.program_id != program.id:
            raise serializers.ValidationError({"household": "Household must be registered in the selected program"})
        if beneficiary and beneficiary.household_id != household.id:
            raise serializers.ValidationError({"beneficiary": "Member must belong to the selected household"})
        if not warehouse:
            raise serializers.ValidationError({"warehouse": "Warehouse is required for NFI entitlement"})
        if item.program_id != program.id or warehouse.program_id != program.id or item.warehouse_id != warehouse.id:
            raise serializers.ValidationError("Item and warehouse must belong to the selected program")
        if not program.nfi_enabled:
            raise serializers.ValidationError({"program": "NFI assistance is disabled for this program"})
        if attrs.get("quantity", self.instance.quantity if self.instance else 0) < 1:
            raise serializers.ValidationError({"quantity": "NFI entitlement quantity must be greater than zero"})
        if not item.active or (item.available_quantity < 1 and self.instance is None):
            raise serializers.ValidationError({"item": "Item is inactive or has no available stock"})
        if not approved_household_modality(
            household,
            program,
            [Enrollment.AssistanceModality.NFI, Enrollment.AssistanceModality.CASH_NFI],
            include_legacy=self.instance is not None,
        ):
            raise serializers.ValidationError({"household": "Household is not approved for NFI assistance"})
        if not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        attrs["household"] = household
        return attrs

    def get_household_reference(self, obj):
        return household_reference(obj.household or getattr(obj.beneficiary, "household", None))

    def get_household_member_count(self, obj):
        return household_member_count(obj.household or getattr(obj.beneficiary, "household", None))

    def get_household_size(self, obj):
        household = obj.household or getattr(obj.beneficiary, "household", None)
        return household.household_size if household else None

    def get_household_members(self, obj):
        return household_member_details(obj.household or getattr(obj.beneficiary, "household", None))


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
    household_reference = serializers.SerializerMethodField()
    household_member_count = serializers.SerializerMethodField()
    household_size = serializers.SerializerMethodField()
    household_members = serializers.SerializerMethodField()
    beneficiary_name = serializers.CharField(source="beneficiary.full_name", read_only=True)
    national_id_reference = serializers.SerializerMethodField()
    item_name = serializers.CharField(source="item.name", read_only=True)
    program_name = serializers.CharField(source="event.program.name", read_only=True)
    warehouse_name = serializers.CharField(source="event.warehouse.name", read_only=True, allow_null=True)
    event_date = serializers.DateField(source="event.event_date", read_only=True)
    event_location = serializers.CharField(source="event.location", read_only=True)

    class Meta:
        model = DistributionAllocation
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]

    def get_national_id_reference(self, allocation):
        return safe_beneficiary_reference(allocation.beneficiary) if allocation.beneficiary else "Not recorded"

    def validate(self, attrs):
        entitlement = attrs.get("entitlement") or self.instance.entitlement
        event = attrs.get("event") or self.instance.event
        item = attrs.get("item") or self.instance.item
        beneficiary = attrs.get("beneficiary") or getattr(self.instance, "beneficiary", None)
        household = attrs.get("household") or getattr(self.instance, "household", None) or entitlement.household or getattr(beneficiary, "household", None)
        planned = attrs.get("planned_quantity", self.instance.planned_quantity if self.instance else 0)
        allocated = attrs.get("allocated_quantity", self.instance.allocated_quantity if self.instance else 0)
        if not household or entitlement.household_id != household.id or entitlement.item_id != item.id:
            raise serializers.ValidationError("Household and item must match the NFI entitlement")
        if beneficiary and beneficiary.household_id != household.id:
            raise serializers.ValidationError("Member must belong to the selected household")
        if event.program_id != entitlement.program_id or event.warehouse_id != entitlement.warehouse_id:
            raise serializers.ValidationError("Distribution event must match the entitlement program and warehouse")
        already_allocated = DistributionAllocation.objects.filter(entitlement=entitlement).exclude(pk=getattr(self.instance, "pk", None)).aggregate(total=Sum("allocated_quantity"))["total"] or 0
        if planned != allocated or allocated < 1 or already_allocated + allocated > entitlement.quantity:
            raise serializers.ValidationError("Allocation must use the exact remaining entitlement quantity")
        attrs["household"] = household
        return attrs

    def get_household_reference(self, allocation):
        return household_reference(allocation.household or allocation.entitlement.household)

    def get_household_member_count(self, allocation):
        return household_member_count(allocation.household or allocation.entitlement.household)

    def get_household_size(self, allocation):
        household = allocation.household or allocation.entitlement.household
        return household.household_size if household else None

    def get_household_members(self, allocation):
        return household_member_details(allocation.household or allocation.entitlement.household)


class DistributionIssueSerializer(serializers.ModelSerializer):
    household_reference = serializers.SerializerMethodField()
    household_member_count = serializers.SerializerMethodField()
    household_size = serializers.SerializerMethodField()
    household_members = serializers.SerializerMethodField()
    beneficiary_name = serializers.CharField(source="beneficiary.full_name", read_only=True)
    national_id_reference = serializers.SerializerMethodField()
    item_name = serializers.CharField(source="item.name", read_only=True)
    program_name = serializers.CharField(source="event.program.name", read_only=True)
    warehouse_name = serializers.CharField(source="event.warehouse.name", read_only=True, allow_null=True)
    event_date = serializers.DateField(source="event.event_date", read_only=True)
    event_location = serializers.CharField(source="event.location", read_only=True)

    class Meta:
        model = DistributionIssue
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]

    def get_national_id_reference(self, issue):
        return safe_beneficiary_reference(issue.beneficiary) if issue.beneficiary else "Not recorded"

    def validate(self, attrs):
        entitlement = attrs.get("entitlement") or self.instance.entitlement
        event = attrs.get("event") or self.instance.event
        beneficiary = attrs.get("beneficiary") or getattr(self.instance, "beneficiary", None)
        household = attrs.get("household") or getattr(self.instance, "household", None) or entitlement.household or getattr(beneficiary, "household", None)
        item = attrs.get("item") or self.instance.item
        planned = attrs.get("planned_quantity", self.instance.planned_quantity if self.instance else None)
        actual = attrs.get("actual_quantity", self.instance.actual_quantity if self.instance else 0)
        if not household or entitlement.household_id != household.id or entitlement.item_id != item.id:
            raise serializers.ValidationError("Household and item must match the entitlement")
        if beneficiary and beneficiary.household_id != household.id:
            raise serializers.ValidationError("Member must belong to the selected household")
        if event.program_id != entitlement.program_id or (event.warehouse_id and event.warehouse_id != entitlement.warehouse_id):
            raise serializers.ValidationError("Distribution event must match the entitlement program and warehouse")
        if not event.warehouse_id:
            raise serializers.ValidationError({"event": "Distribution event must have a warehouse"})
        allocation = DistributionAllocation.objects.filter(entitlement=entitlement, event=event, household=household, item=item).first()
        if not allocation:
            raise serializers.ValidationError("Delivery review requires an allocation from this distribution event")
        already_delivered = DistributionIssue.objects.filter(entitlement=entitlement, event=event).exclude(pk=getattr(self.instance, "pk", None)).aggregate(total=Sum("actual_quantity"))["total"] or 0
        if planned != allocation.allocated_quantity or planned < 1 or actual > planned or already_delivered + actual > allocation.allocated_quantity:
            raise serializers.ValidationError("Actual delivery cannot exceed the planned entitlement quantity")
        user = self.context["request"].user
        if not user.is_superuser and event.program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Distribution event belongs to another tenant")
        attrs["household"] = household
        return attrs

    def get_household_reference(self, issue):
        return household_reference(issue.household or issue.entitlement.household)

    def get_household_member_count(self, issue):
        return household_member_count(issue.household or issue.entitlement.household)

    def get_household_size(self, issue):
        household = issue.household or issue.entitlement.household
        return household.household_size if household else None

    def get_household_members(self, issue):
        return household_member_details(issue.household or issue.entitlement.household)


class PaymentDeliveryReviewSerializer(serializers.ModelSerializer):
    household_reference = serializers.SerializerMethodField()
    household_size = serializers.IntegerField(source="household.household_size", read_only=True)
    household_members = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="program.name", read_only=True)
    reviewer_name = serializers.CharField(source="reviewed_by.full_name", read_only=True)
    payment_amount = serializers.DecimalField(source="payment_instruction.amount", max_digits=18, decimal_places=2, read_only=True)
    fee_amount = serializers.DecimalField(source="payment_instruction.applied_fee", max_digits=18, decimal_places=2, read_only=True)
    total_financial_impact = serializers.SerializerMethodField()
    channel_name = serializers.CharField(source="payment_instruction.channel_config.provider_name", read_only=True)
    payment_status = serializers.CharField(source="payment_instruction.status", read_only=True)
    provider_reference = serializers.CharField(source="payment_instruction.provider_reference", read_only=True)

    class Meta:
        model = PaymentDeliveryReview
        fields = "__all__"
        read_only_fields = ["household", "program", "reviewed_by", "reviewed_at", "created_at", "updated_at"]

    def validate_payment_instruction(self, instruction):
        if not instruction.cash_entitlement_id:
            raise serializers.ValidationError("Payment delivery review requires a household cash entitlement")
        if instruction.status not in {
            PaymentInstruction.Status.SUCCESS,
            PaymentInstruction.Status.FAILED,
            PaymentInstruction.Status.REVERSED,
        }:
            raise serializers.ValidationError("Only completed payment attempts can be reviewed")
        user = self.context["request"].user
        program = instruction.cash_entitlement.program
        if not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Payment belongs to another tenant")
        return instruction

    def get_household_reference(self, review):
        return household_reference(review.household)

    def get_household_members(self, review):
        return household_member_details(review.household)

    def get_total_financial_impact(self, review):
        return review.payment_instruction.amount + review.payment_instruction.applied_fee


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
        return household_reference(household)

    def get_ai_analysis_available(self, complaint):
        return ComplaintAIAnalysis.objects.filter(complaint=complaint).exists()


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
