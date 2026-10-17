import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone as django_timezone

from accounts.models import Tenant, User
from programs.models import Program

# Households table for registered families inside a program.
class Household(models.Model):
    class ParticipationStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="households")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="households")
    household_size = models.PositiveIntegerField()
    location = models.CharField(max_length=255)
    registration_date = models.DateField()
    registration_reference = models.CharField(max_length=120, blank=True)
    participation_status = models.CharField(
        max_length=20,
        choices=ParticipationStatus.choices,
        default=ParticipationStatus.ACTIVE,
    )
    withdrawal_reason = models.TextField(blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="withdrawn_households",
    )
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_households")
    created_at = models.DateTimeField(default=django_timezone.now)

    def __init__(self, *args, **kwargs):
        if "client_generated_id" in kwargs and "registration_reference" not in kwargs:
            kwargs["registration_reference"] = kwargs.pop("client_generated_id")
        super().__init__(*args, **kwargs)

    class Meta:
        db_table = "core_household"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "registration_reference"],
                condition=~models.Q(registration_reference=""),
                name="unique_household_registration_reference_per_tenant",
            )
        ]

    @property
    def client_generated_id(self):
        """Legacy read alias; registration_reference is authoritative."""
        return self.registration_reference

    @client_generated_id.setter
    def client_generated_id(self, value):
        self.registration_reference = value or ""


# Beneficiaries table for individual household members and their verification data.
class Beneficiary(models.Model):
    class VerificationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VERIFIED = "VERIFIED", "Verified"
        REJECTED = "REJECTED", "Rejected"

    class Status(models.TextChoices):
        REGISTERED = "REGISTERED", "Registered"
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        EXITED = "EXITED", "Exited"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.PROTECT, related_name="beneficiaries")
    number = models.CharField(max_length=80)
    full_name = models.CharField(max_length=255)
    gender = models.CharField(max_length=40, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    phone_number = models.CharField(max_length=30, blank=True)
    national_id_hash = models.CharField(max_length=255, blank=True)
    consent_given = models.BooleanField(default=False)
    verification_status = models.CharField(max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.PENDING)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REGISTERED)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_beneficiaries")
    created_at = models.DateTimeField(default=django_timezone.now)

    def __init__(self, *args, **kwargs):
        legacy_phone_last4 = kwargs.pop("phone_last4", None)
        super().__init__(*args, **kwargs)
        if legacy_phone_last4 and not self.phone_number:
            self.phone_number = str(legacy_phone_last4)

    class Meta:
        db_table = "core_beneficiary"
        constraints = [models.UniqueConstraint(fields=["household", "number"], name="unique_beneficiary_number_per_household")]

    @property
    def phone_last4(self):
        return (self.phone_number or "")[-4:]


# Beneficiary enrollments table with approval and assistance modality decisions.
class Enrollment(models.Model):
    class AssistanceModality(models.TextChoices):
        CASH = "CASH", "Payment"
        NFI = "NFI", "NFI"
        CASH_NFI = "CASH_NFI", "Payment + NFI"
        MEDICAL = "MEDICAL", "Medical Assistance"
    class EligibilityStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ELIGIBLE = "ELIGIBLE", "Eligible"
        INELIGIBLE = "INELIGIBLE", "Ineligible"
        ON_HOLD = "ON_HOLD", "On hold"

    class Status(models.TextChoices):
        ENROLLED = "ENROLLED", "Enrolled"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        SUSPENDED = "SUSPENDED", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.PROTECT, related_name="legacy_enrollments", null=True, blank=True)
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.PROTECT, related_name="enrollments", null=True, blank=True)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="enrollments")
    eligibility_status = models.CharField(max_length=20, choices=EligibilityStatus.choices, default=EligibilityStatus.PENDING)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ENROLLED)
    assistance_modality = models.CharField(max_length=12, choices=AssistanceModality.choices, default=AssistanceModality.CASH)
    enrolled_at = models.DateTimeField(default=django_timezone.now)
    approved_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_enrollments")
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "core_enrollment"
        constraints = [models.UniqueConstraint(fields=["program", "beneficiary"], name="unique_enrollment_per_program_beneficiary")]


# Records the human eligibility decision for a household and program.
class HouseholdEligibility(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ELIGIBLE = "ELIGIBLE", "Eligible"
        INELIGIBLE = "INELIGIBLE", "Ineligible"
        ON_HOLD = "ON_HOLD", "On hold"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.PROTECT, related_name="eligibility_decisions")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="household_eligibility")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    note = models.TextField(blank=True)
    decided_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="household_eligibility_decisions")
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=django_timezone.now)

    class Meta:
        db_table = "core_householdeligibility"


# Records household-level enrollment before assistance is delivered.
class HouseholdEnrollment(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ENROLLED = "ENROLLED", "Enrolled"
        ACCEPTED = "ACCEPTED", "Accepted"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        SUSPENDED = "SUSPENDED", "Suspended"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.PROTECT, related_name="household_enrollments")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="household_enrollments")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    assistance_modality = models.CharField(max_length=12, choices=Enrollment.AssistanceModality.choices, default=Enrollment.AssistanceModality.CASH)
    note = models.TextField(blank=True)
    decided_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="household_enrollment_decisions")
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=django_timezone.now)

    class Meta:
        db_table = "core_householdenrollment"


# Stores the approved CASH, NFI, or medical modality for an enrolled household.
class HouseholdModalityDecision(models.Model):
    """Independent approval state for each assistance modality in one enrollment."""

    class Modality(models.TextChoices):
        CASH = "CASH", "Payment"
        NFI = "NFI", "NFI"
        MEDICAL = "MEDICAL", "Medical Assistance"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        SUSPENDED = "SUSPENDED", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.PROTECT, related_name="modality_decisions")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="household_modality_decisions")
    modality = models.CharField(max_length=20, choices=Modality.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    note = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="household_modality_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=django_timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "core_householdmodalitydecision"
        ordering = ["program__name", "household_id", "modality"]
        constraints = [
            models.UniqueConstraint(
                fields=["household", "program", "modality"],
                name="unique_household_program_modality_decision",
            )
        ]


# Cash entitlements table used before a payment instruction is created.
class CashEntitlement(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        PAID = "PAID", "Paid"
        CANCELLED = "CANCELLED", "Cancelled"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.PROTECT, related_name="cash_entitlements", null=True, blank=True)
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.PROTECT, related_name="cash_entitlements", null=True, blank=True)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="cash_entitlements")
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(0.01)])
    currency = models.CharField(max_length=3)
    conditions = models.TextField(blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    cancellation_reason = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cancelled_cash_entitlements",
    )
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_cash_entitlements")
    created_at = models.DateTimeField(default=django_timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "core_cashentitlement"

