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



class HouseholdEligibilitySerializer(serializers.ModelSerializer):
    beneficiaries = serializers.SerializerMethodField()
    household_reference = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="program.name", read_only=True)
    class Meta:
        model = HouseholdEligibility
        fields = "__all__"
        read_only_fields = ["decided_by", "decided_at", "created_at"]
    def get_beneficiaries(self, obj):
        return list(obj.household.beneficiaries.values("id", "full_name", "number", "status"))

    def get_household_reference(self, obj):
        return household_reference(obj.household)

    def get_member_count(self, obj):
        return household_member_count(obj.household)

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
    household_reference = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    household_size = serializers.SerializerMethodField()
    household_members = serializers.SerializerMethodField()
    eligibility_status = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="program.name", read_only=True)
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
        submitted_modality = self.initial_data.get("assistance_modality") if self.initial_data is not None else None
        modality = attrs.get("assistance_modality", self.instance.assistance_modality if self.instance else None)
        if not submitted_modality and not self.instance and program.nfi_enabled and not program.cash_enabled:
            modality = Enrollment.AssistanceModality.NFI
            attrs["assistance_modality"] = modality
        if modality == Enrollment.AssistanceModality.CASH and not program.cash_enabled:
            raise serializers.ValidationError({"assistance_modality": "Payment is disabled for this program"})
        if modality == Enrollment.AssistanceModality.NFI and not program.nfi_enabled:
            raise serializers.ValidationError({"assistance_modality": "NFI is disabled for this program"})
        if modality == Enrollment.AssistanceModality.CASH_NFI and not (program.cash_enabled and program.nfi_enabled):
            raise serializers.ValidationError({"assistance_modality": "Payment + NFI requires both modalities to be enabled"})
        return attrs

    def get_household_size(self, obj):
        return obj.household.household_size

    def get_household_members(self, obj):
        return household_member_details(obj.household)

    def get_eligibility_status(self, obj):
        decision = obj.household.eligibility_decisions.filter(
            program_id=obj.program_id
        ).order_by("-decided_at", "-created_at").first()
        return decision.status if decision else None

    def get_household_reference(self, obj):
        return household_reference(obj.household)

    def get_member_count(self, obj):
        return household_member_count(obj.household)


class CashEntitlementSerializer(serializers.ModelSerializer):
    household_reference = serializers.SerializerMethodField()
    household_member_count = serializers.SerializerMethodField()
    household_size = serializers.SerializerMethodField()
    household_members = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="program.name", read_only=True)
    budget_total = serializers.SerializerMethodField()
    budget_committed = serializers.SerializerMethodField()
    budget_remaining = serializers.SerializerMethodField()
    class Meta:
        model = CashEntitlement
        fields = "__all__"
        read_only_fields = ["created_by", "created_at", "updated_at"]

    def validate(self, attrs):
        beneficiary = attrs.get("beneficiary") or getattr(self.instance, "beneficiary", None)
        household = attrs.get("household") or getattr(self.instance, "household", None) or getattr(beneficiary, "household", None)
        program = attrs.get("program") or getattr(self.instance, "program", None)
        user = self.context["request"].user
        if not household or household.program_id != program.id:
            raise serializers.ValidationError({"household": "Household must be registered in the selected program"})
        if beneficiary and beneficiary.household_id != household.id:
            raise serializers.ValidationError({"beneficiary": "Member must belong to the selected household"})
        if not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        if not program.cash_enabled:
            raise serializers.ValidationError({"program": "Cash assistance is disabled for this program"})
        currency = attrs.get("currency", getattr(self.instance, "currency", None))
        if currency != program.currency:
            raise serializers.ValidationError({"currency": "Entitlement currency must match the program currency"})
        if not approved_household_modality(
            household,
            program,
            [Enrollment.AssistanceModality.CASH, Enrollment.AssistanceModality.CASH_NFI],
            include_legacy=self.instance is not None,
        ):
            raise serializers.ValidationError({"household": "Household is not approved for cash assistance"})
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

    def get_budget_total(self, obj):
        return getattr(getattr(obj.program, "budget", None), "planned_total", 0)

    def get_budget_committed(self, obj):
        return CashEntitlement.objects.filter(program=obj.program, status__in=[CashEntitlement.Status.ACTIVE, CashEntitlement.Status.PAID]).aggregate(total=Sum("amount"))["total"] or 0

    def get_budget_remaining(self, obj):
        successful_fees = PaymentInstruction.objects.filter(
            cash_entitlement__program=obj.program,
            status=PaymentInstruction.Status.SUCCESS,
        ).aggregate(total=Sum("applied_fee"))["total"] or 0
        return (self.get_budget_total(obj) or 0) - (self.get_budget_committed(obj) or 0) - successful_fees


class HouseholdSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    registration_reference = serializers.SerializerMethodField()

    class Meta:
        model = Household
        fields = "__all__"
        read_only_fields = ["tenant", "created_by", "created_at"]

    def validate_program(self, program):
        if program.tenant_id != self.context["request"].user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        return program

    def validate(self, attrs):
        household_size = attrs.get(
            "household_size",
            self.instance.household_size if self.instance else None,
        )
        if (
            self.instance
            and household_size is not None
            and household_size < self.instance.beneficiaries.count()
        ):
            raise serializers.ValidationError(
                {
                    "household_size": (
                        f"Household size cannot be less than its current "
                        f"{self.instance.beneficiaries.count()} beneficiaries."
                    )
                }
            )
        return attrs

    def get_registration_reference(self, household):
        return household_reference(household)


class BeneficiarySerializer(serializers.ModelSerializer):
    number = serializers.CharField(write_only=True, required=False)
    national_id = serializers.CharField(write_only=True, required=False)
    national_id_hash = serializers.CharField(write_only=True, required=False)
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
        household = attrs.get("household") or getattr(self.instance, "household", None)
        if household and self.instance is None:
            member_count = household.beneficiaries.count()
            if member_count >= household.household_size:
                raise serializers.ValidationError(
                    {
                        "household": (
                            f"This household already has {member_count} "
                            f"beneficiaries (maximum {household.household_size})."
                        )
                    }
                )
        return attrs

    def get_household_reference(self, beneficiary):
        household = beneficiary.household
        return household_reference(household)

    def get_national_id_reference(self, beneficiary):
        return safe_beneficiary_reference(beneficiary)
        return f"•••• {beneficiary.number[-4:]}" if beneficiary.number else "Not recorded"

    def get_masked_phone(self, beneficiary):
        phone_number = beneficiary.phone_number
        return f"**** {phone_number[-4:]}" if phone_number else "Not recorded"


class EnrollmentSerializer(serializers.ModelSerializer):
    household_reference = serializers.SerializerMethodField()
    household_member_count = serializers.SerializerMethodField()
    beneficiary_name = serializers.CharField(source="beneficiary.full_name", read_only=True)
    beneficiary_number = serializers.SerializerMethodField()
    national_id_reference = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="program.name", read_only=True)

    class Meta:
        model = Enrollment
        fields = "__all__"
        read_only_fields = ["approved_by", "approved_at", "enrolled_at"]

    def get_beneficiary_number(self, enrollment):
        return safe_beneficiary_reference(enrollment.beneficiary) if enrollment.beneficiary else "Not recorded"

    def get_national_id_reference(self, enrollment):
        return safe_beneficiary_reference(enrollment.beneficiary) if enrollment.beneficiary else "Not recorded"

    def get_household_reference(self, enrollment):
        return household_reference(enrollment.household or getattr(enrollment.beneficiary, "household", None))

    def get_household_member_count(self, enrollment):
        return household_member_count(enrollment.household or getattr(enrollment.beneficiary, "household", None))

    def validate(self, attrs):
        beneficiary = attrs.get("beneficiary") or getattr(self.instance, "beneficiary", None)
        household = attrs.get("household") or getattr(self.instance, "household", None) or getattr(beneficiary, "household", None)
        program = attrs.get("program") or getattr(self.instance, "program", None)
        user = self.context["request"].user
        if not household or household.program_id != program.id:
            raise serializers.ValidationError({"household": "Household must be registered in the selected program"})
        if beneficiary and beneficiary.household_id != household.id:
            raise serializers.ValidationError({"beneficiary": "Member must belong to the selected household"})
        if household.tenant_id != program.tenant_id:
            raise serializers.ValidationError("Household and program must belong to the same tenant")
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
        attrs["household"] = household
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
