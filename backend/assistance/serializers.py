from decimal import Decimal

from django.contrib.auth import authenticate
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import serializers

from accounts.models import IdempotencyRecord, Tenant, User
from assistance.models import (
    Beneficiary,
    CashEntitlement,
    Enrollment,
    Household,
    HouseholdEligibility,
    HouseholdEnrollment,
    HouseholdModalityDecision,
)
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



# Validates and returns household eligibility decisions.
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


class HouseholdModalityDecisionInputSerializer(serializers.Serializer):
    modality = serializers.ChoiceField(choices=HouseholdModalityDecision.Modality.choices)
    status = serializers.ChoiceField(choices=HouseholdModalityDecision.Status.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class HouseholdModalityDecisionSerializer(serializers.ModelSerializer):
    household_reference = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="program.name", read_only=True)
    decided_by_name = serializers.CharField(source="decided_by.full_name", read_only=True, allow_null=True)

    class Meta:
        model = HouseholdModalityDecision
        fields = "__all__"
        read_only_fields = ["decided_by", "decided_at", "created_at", "updated_at"]

    def get_household_reference(self, obj):
        return household_reference(obj.household)

    def validate(self, attrs):
        household = attrs.get("household") or self.instance.household
        program = attrs.get("program") or self.instance.program
        modality = attrs.get("modality") or self.instance.modality
        user = self.context["request"].user
        if household.program_id != program.id:
            raise serializers.ValidationError({"program": "Program must match the household program"})
        if not user.is_superuser and program.tenant_id != user.tenant_id:
            raise serializers.ValidationError("Program belongs to another tenant")
        enabled = {
            HouseholdModalityDecision.Modality.CASH: program.cash_enabled,
            HouseholdModalityDecision.Modality.NFI: program.nfi_enabled,
            HouseholdModalityDecision.Modality.MEDICAL: program.medical_enabled,
        }
        if not enabled[modality]:
            raise serializers.ValidationError({"modality": f"{modality.title()} assistance is disabled for this program"})
        return attrs


# Validates household enrollment and the selected assistance modality.
class HouseholdEnrollmentSerializer(serializers.ModelSerializer):
    household_reference = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    household_size = serializers.SerializerMethodField()
    household_members = serializers.SerializerMethodField()
    eligibility_status = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="program.name", read_only=True)
    modality_decisions = HouseholdModalityDecisionInputSerializer(many=True, required=False, write_only=True)
    modality_decision_details = serializers.SerializerMethodField()

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
        decisions = attrs.get("modality_decisions")
        if decisions:
            modalities = [decision["modality"] for decision in decisions]
            if len(modalities) != len(set(modalities)):
                raise serializers.ValidationError({"modality_decisions": "Each modality may be decided only once."})
            enabled = {
                HouseholdModalityDecision.Modality.CASH: program.cash_enabled,
                HouseholdModalityDecision.Modality.NFI: program.nfi_enabled,
                HouseholdModalityDecision.Modality.MEDICAL: program.medical_enabled,
            }
            disabled = [modality for modality in modalities if not enabled[modality]]
            if disabled:
                raise serializers.ValidationError(
                    {"modality_decisions": f"Disabled program modalities cannot be selected: {', '.join(disabled)}"}
                )
            modality_set = set(modalities)
            if modality_set == {
                HouseholdModalityDecision.Modality.CASH,
                HouseholdModalityDecision.Modality.NFI,
            }:
                attrs["assistance_modality"] = Enrollment.AssistanceModality.CASH_NFI
            elif HouseholdModalityDecision.Modality.MEDICAL in modality_set:
                attrs["assistance_modality"] = Enrollment.AssistanceModality.MEDICAL
            elif HouseholdModalityDecision.Modality.NFI in modality_set:
                attrs["assistance_modality"] = Enrollment.AssistanceModality.NFI
            else:
                attrs["assistance_modality"] = Enrollment.AssistanceModality.CASH
            statuses = {decision["status"] for decision in decisions}
            if HouseholdModalityDecision.Status.APPROVED in statuses:
                attrs["status"] = HouseholdEnrollment.Status.APPROVED
            elif statuses == {HouseholdModalityDecision.Status.REJECTED}:
                attrs["status"] = HouseholdEnrollment.Status.REJECTED
            elif HouseholdModalityDecision.Status.SUSPENDED in statuses:
                attrs["status"] = HouseholdEnrollment.Status.SUSPENDED
            else:
                attrs["status"] = HouseholdEnrollment.Status.PENDING
            return attrs
        submitted_modality = self.initial_data.get("assistance_modality") if self.initial_data is not None else None
        modality = attrs.get("assistance_modality", self.instance.assistance_modality if self.instance else None)
        if not submitted_modality and not self.instance and program.medical_enabled and not program.cash_enabled and not program.nfi_enabled:
            modality = Enrollment.AssistanceModality.MEDICAL
            attrs["assistance_modality"] = modality
        elif not submitted_modality and not self.instance and program.nfi_enabled and not program.cash_enabled:
            modality = Enrollment.AssistanceModality.NFI
            attrs["assistance_modality"] = modality
        if modality == Enrollment.AssistanceModality.CASH and not program.cash_enabled:
            raise serializers.ValidationError({"assistance_modality": "Payment is disabled for this program"})
        if modality == Enrollment.AssistanceModality.NFI and not program.nfi_enabled:
            raise serializers.ValidationError({"assistance_modality": "NFI is disabled for this program"})
        if modality == Enrollment.AssistanceModality.CASH_NFI and not (program.cash_enabled and program.nfi_enabled):
            raise serializers.ValidationError({"assistance_modality": "Payment + NFI requires both modalities to be enabled"})
        if modality == Enrollment.AssistanceModality.MEDICAL and not program.medical_enabled:
            raise serializers.ValidationError({"assistance_modality": "Medical assistance is disabled for this program"})
        return attrs

    def create(self, validated_data):
        decisions = validated_data.pop("modality_decisions", None)
        instance = super().create(validated_data)
        self._save_modality_decisions(instance, decisions)
        return instance

    def update(self, instance, validated_data):
        decisions = validated_data.pop("modality_decisions", None)
        instance = super().update(instance, validated_data)
        self._save_modality_decisions(instance, decisions)
        return instance

    def _save_modality_decisions(self, instance, decisions):
        if decisions is None:
            if instance.assistance_modality == Enrollment.AssistanceModality.CASH_NFI:
                modalities = [HouseholdModalityDecision.Modality.CASH, HouseholdModalityDecision.Modality.NFI]
            else:
                modalities = [instance.assistance_modality]
            status_map = {
                HouseholdEnrollment.Status.ACCEPTED: HouseholdModalityDecision.Status.APPROVED,
                HouseholdEnrollment.Status.APPROVED: HouseholdModalityDecision.Status.APPROVED,
                HouseholdEnrollment.Status.REJECTED: HouseholdModalityDecision.Status.REJECTED,
                HouseholdEnrollment.Status.SUSPENDED: HouseholdModalityDecision.Status.SUSPENDED,
            }
            decisions = [
                {
                    "modality": modality,
                    "status": status_map.get(instance.status, HouseholdModalityDecision.Status.PENDING),
                    "note": instance.note,
                }
                for modality in modalities
            ]
        actor = self.context["request"].user
        for decision in decisions:
            status_value = decision["status"]
            decided = status_value != HouseholdModalityDecision.Status.PENDING
            HouseholdModalityDecision.objects.update_or_create(
                household=instance.household,
                program=instance.program,
                modality=decision["modality"],
                defaults={
                    "status": status_value,
                    "note": decision.get("note", ""),
                    "decided_by": actor if decided else None,
                    "decided_at": timezone.now() if decided else None,
                },
            )

    def get_modality_decision_details(self, obj):
        decisions = obj.household.modality_decisions.filter(program=obj.program).select_related("decided_by")
        return HouseholdModalityDecisionSerializer(decisions, many=True, context=self.context).data

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


# Validates cash entitlement amounts against the selected program and enrollment.
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
        if household.participation_status != Household.ParticipationStatus.ACTIVE:
            raise serializers.ValidationError({"household": "This household has been withdrawn from the program."})
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


# Returns household references and readable program details for the frontend.
class HouseholdSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    registration_reference = serializers.SerializerMethodField()

    class Meta:
        model = Household
        fields = "__all__"
        read_only_fields = [
            "tenant", "created_by", "created_at", "participation_status",
            "withdrawal_reason", "withdrawn_at", "withdrawn_by",
        ]

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


# Protects personal identifiers while returning readable beneficiary details.
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


# Validates beneficiary enrollment, approval, and modality rules.
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
