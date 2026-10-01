import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import MinValueValidator
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone as django_timezone


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    tenant_type = models.CharField(max_length=80)
    default_currency = models.CharField(max_length=3)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=django_timezone.now)

    def __str__(self):
        return self.name


class IdempotencyRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("Tenant", on_delete=models.PROTECT, related_name="idempotency_records")
    actor = models.ForeignKey("User", on_delete=models.PROTECT, related_name="idempotency_records")
    endpoint = models.CharField(max_length=255)
    idempotency_key = models.CharField(max_length=255)
    request_fingerprint = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(default=201)
    response_body = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(default=django_timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "actor", "endpoint", "idempotency_key"], name="unique_idempotency_request")]


class UserManager(BaseUserManager):
    def create_user(self, email, full_name, tenant=None, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email), full_name=full_name, tenant=tenant, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, full_name="Administrator", password=None, **extra_fields):
        tenant = extra_fields.pop("tenant", None) or Tenant.objects.create(
            name="System Tenant", tenant_type="ADMIN", default_currency="USD"
        )
        extra_fields.setdefault("role", User.Role.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, full_name, tenant, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        FIELD_OFFICER = "FIELD_OFFICER", "Field officer"
        FINANCE = "FINANCE", "Finance"
        REVIEWER = "REVIEWER", "Reviewer"
        SUPPORT = "SUPPORT", "Support"
        MANAGER = "MANAGER", "Manager"
        AUDITOR = "AUDITOR", "Auditor"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="users")
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255)
    role = models.CharField(max_length=32, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=django_timezone.now)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]
    objects = UserManager()

    def __str__(self):
        return self.email


class Program(models.Model):
    class Cycle(models.TextChoices):
        ONE_TIME = "ONE_TIME", "One time"
        MONTHLY = "MONTHLY", "Monthly"
        QUARTERLY = "QUARTERLY", "Quarterly"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACTIVE = "ACTIVE", "Active"
        PAUSED = "PAUSED", "Paused"
        CLOSED = "CLOSED", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="programs")
    name = models.CharField(max_length=255)
    country = models.CharField(max_length=120)
    country_code = models.CharField(max_length=2, default="SD")
    currency = models.CharField(max_length=3)
    currency_type = models.CharField(max_length=80, default="PROGRAM")
    reporting_currency = models.CharField(max_length=3, blank=True)
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=1)
    timezone = models.CharField(max_length=80, default="UTC")
    language = models.CharField(max_length=10, default="en")
    transfer_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0.01)])
    payment_cycle = models.CharField(max_length=20, choices=Cycle.choices, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    workflow_config = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    country_pack = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_programs")
    created_at = models.DateTimeField(default=django_timezone.now)
    cash_enabled = models.BooleanField(default=True)
    nfi_enabled = models.BooleanField(default=False)
    other_assistance_enabled = models.BooleanField(default=False)


class Household(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="households")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="households")
    household_size = models.PositiveIntegerField()
    location = models.CharField(max_length=255)
    registration_date = models.DateField()
    client_generated_id = models.CharField(max_length=120, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_households")
    created_at = models.DateTimeField(default=django_timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "client_generated_id"],
                condition=~models.Q(client_generated_id=""),
                name="unique_household_client_id_per_tenant",
            )
        ]


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
    phone_last4 = models.CharField(max_length=4, blank=True)
    national_id_hash = models.CharField(max_length=255, blank=True)
    consent_given = models.BooleanField(default=False)
    verification_status = models.CharField(max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.PENDING)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REGISTERED)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_beneficiaries")
    created_at = models.DateTimeField(default=django_timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["household", "number"], name="unique_beneficiary_number_per_household")]


class Enrollment(models.Model):
    class AssistanceModality(models.TextChoices):
        CASH = "CASH", "Payment"
        NFI = "NFI", "NFI"
        CASH_NFI = "CASH_NFI", "Payment + NFI"
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
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.PROTECT, related_name="enrollments")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="enrollments")
    eligibility_status = models.CharField(max_length=20, choices=EligibilityStatus.choices, default=EligibilityStatus.PENDING)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ENROLLED)
    assistance_modality = models.CharField(max_length=12, choices=AssistanceModality.choices, default=AssistanceModality.CASH)
    enrolled_at = models.DateTimeField(default=django_timezone.now)
    approved_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_enrollments")
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["program", "beneficiary"], name="unique_enrollment_per_program_beneficiary")]


class PaymentChannelConfig(models.Model):
    class ChannelType(models.TextChoices):
        BANK = "BANK", "Bank"
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile money"
        CASH = "CASH", "Cash"
        VOUCHER = "VOUCHER", "Voucher"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="channels")
    channel_type = models.CharField(max_length=20, choices=ChannelType.choices)
    provider_name = models.CharField(max_length=255)
    currency = models.CharField(max_length=3)
    is_active = models.BooleanField(default=True)


class PaymentInstruction(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        CREATED = "CREATED", "Created"
        SUBMITTED = "SUBMITTED", "Submitted"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        REVERSED = "REVERSED", "Reversed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey("PaymentBatch", on_delete=models.PROTECT, null=True, blank=True, related_name="instructions")
    enrollment = models.ForeignKey(Enrollment, on_delete=models.PROTECT, related_name="payment_instructions")
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.PROTECT, related_name="payment_instructions")
    channel_config = models.ForeignKey(PaymentChannelConfig, on_delete=models.PROTECT, related_name="payment_instructions")
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(0.01)])
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    provider_reference = models.CharField(max_length=120, blank=True)
    idempotency_key = models.CharField(max_length=160, unique=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_payment_instructions")
    created_at = models.DateTimeField(default=django_timezone.now)


class PaymentEvent(models.Model):
    class EventType(models.TextChoices):
        CREATED = "CREATED", "Created"
        SUBMITTED = "SUBMITTED", "Submitted"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        REVERSED = "REVERSED", "Reversed"
        REFUNDED = "REFUNDED", "Refunded"
        RETRY = "RETRY", "Retry"
        EXCEPTION = "EXCEPTION", "Exception"

    class ProviderStatus(models.TextChoices):
        NOT_SENT = "NOT_SENT", "Not sent"
        ACCEPTED = "ACCEPTED", "Accepted"
        REJECTED = "REJECTED", "Rejected"
        SETTLED = "SETTLED", "Settled"
        FAILED = "FAILED", "Failed"
        UNKNOWN = "UNKNOWN", "Unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    instruction = models.ForeignKey(PaymentInstruction, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    provider_status = models.CharField(max_length=20, choices=ProviderStatus.choices, default=ProviderStatus.NOT_SENT)
    provider_transaction_id = models.CharField(max_length=160, blank=True)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    redacted_payload = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    recorded_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="payment_events")
    created_at = models.DateTimeField(default=django_timezone.now)


class Complaint(models.Model):
    class Category(models.TextChoices):
        PAYMENT = "PAYMENT", "Payment"
        ACCESS = "ACCESS", "Access"
        ELIGIBILITY = "ELIGIBILITY", "Eligibility"

    class Severity(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        RESOLVED = "RESOLVED", "Resolved"
        CLOSED = "CLOSED", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.PROTECT, related_name="complaints")
    instruction = models.ForeignKey(PaymentInstruction, on_delete=models.PROTECT, null=True, blank=True, related_name="complaints")
    category = models.CharField(max_length=120, choices=Category.choices)
    description = models.TextField()
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="assigned_complaints")
    resolution_notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_complaints")
    created_at = models.DateTimeField(default=django_timezone.now)


class ComplaintAIAnalysis(models.Model):
    class ReviewDecision(models.TextChoices):
        ACCEPT = "ACCEPT", "Accept"
        MODIFY = "MODIFY", "Modify"
        DISMISS = "DISMISS", "Dismiss"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    complaint = models.OneToOneField(Complaint, on_delete=models.PROTECT, related_name="ai_analysis")
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="complaint_ai_analyses")
    category = models.CharField(max_length=120)
    severity = models.CharField(max_length=20, choices=Complaint.Severity.choices)
    summary = models.TextField()
    possible_causes = models.JSONField(default=list, blank=True, encoder=DjangoJSONEncoder)
    recommended_actions = models.JSONField(default=list, blank=True, encoder=DjangoJSONEncoder)
    evidence = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    confidence = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    requires_escalation = models.BooleanField(default=False)
    model_version = models.CharField(max_length=80)
    review_decision = models.CharField(max_length=20, choices=ReviewDecision.choices, blank=True)
    reviewer_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="reviewed_complaint_ai_analyses")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=django_timezone.now)
    updated_at = models.DateTimeField(auto_now=True)


class PDMResponse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="pdm_responses")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="pdm_responses")
    channel = models.CharField(max_length=120)
    location = models.CharField(max_length=255)
    received_count = models.PositiveIntegerField(default=0)
    received_rate = models.DecimalField(max_digits=5, decimal_places=2)
    amount_received = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    access_problem_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    complaint_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    satisfaction = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_pdm_responses")
    created_at = models.DateTimeField(default=django_timezone.now)


class ProgramActivity(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = "NOT_STARTED", "Not started"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        BLOCKED = "BLOCKED", "Blocked"
        COMPLETED = "COMPLETED", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="activities")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="activities")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    workstream = models.CharField(max_length=120, blank=True)
    owner = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="assigned_activities")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    planned_start = models.DateField(null=True, blank=True)
    planned_end = models.DateField(null=True, blank=True)
    actual_start = models.DateField(null=True, blank=True)
    actual_end = models.DateField(null=True, blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    risk = models.CharField(max_length=20, blank=True)
    is_milestone = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_activities")
    created_at = models.DateTimeField(default=django_timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(progress__gte=0) & models.Q(progress__lte=100),
                name="program_activity_progress_range",
            ),
        ]


class ActivityDependency(models.Model):
    class Relationship(models.TextChoices):
        FINISH_TO_START = "FS", "Finish to start"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    predecessor = models.ForeignKey(ProgramActivity, on_delete=models.PROTECT, related_name="successor_dependencies")
    successor = models.ForeignKey(ProgramActivity, on_delete=models.PROTECT, related_name="predecessor_dependencies")
    relationship_type = models.CharField(max_length=10, choices=Relationship.choices, default=Relationship.FINISH_TO_START)
    lag_days = models.IntegerField(default=0)
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_activity_dependencies")
    created_at = models.DateTimeField(default=django_timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["predecessor", "successor"], name="unique_activity_dependency")]


class Budget(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACTIVE = "ACTIVE", "Active"
        CLOSED = "CLOSED", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.OneToOneField(Program, on_delete=models.PROTECT, related_name="budget")
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    planned_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    actual_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    line_items = models.JSONField(default=list, blank=True, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(default=django_timezone.now)


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="audit_events")
    actor = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="audit_events")
    action = models.CharField(max_length=120)
    entity_type = models.CharField(max_length=120)
    entity_id = models.CharField(max_length=120)
    before = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    after = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    correlation_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(default=django_timezone.now)

    class Meta:
        ordering = ["-created_at"]


class AISignal(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        REVIEWED = "REVIEWED", "Reviewed"
        DISMISSED = "DISMISSED", "Dismissed"
        ACCEPTED = "ACCEPTED", "Accepted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="ai_signals")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, null=True, blank=True, related_name="ai_signals")
    entity_type = models.CharField(max_length=120)
    entity_id = models.CharField(max_length=120)
    signal_type = models.CharField(max_length=120)
    score = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    confidence = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    reason = models.TextField()
    evidence = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    model_version = models.CharField(max_length=80, blank=True)
    rule_version = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    reviewed_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="reviewed_ai_signals")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=django_timezone.now)


class PaymentBatch(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        PARTIAL_SUCCESS = "PARTIAL_SUCCESS", "Partial success"
        PARTIAL_FAILURE = "PARTIAL_FAILURE", "Partial failure"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        RECONCILED = "RECONCILED", "Reconciled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="payment_batches")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="payment_batches")
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)
    provider_response = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    idempotency_key = models.CharField(max_length=160, unique=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_payment_batches")
    created_at = models.DateTimeField(default=django_timezone.now)


class ReconciliationItem(models.Model):
    class IssueType(models.TextChoices):
        AMOUNT_MISMATCH = "AMOUNT_MISMATCH", "Amount mismatch"
        STATUS_MISMATCH = "STATUS_MISMATCH", "Status mismatch"
        MISSING_REFERENCE = "MISSING_REFERENCE", "Missing reference"
        FAILED = "FAILED", "Failed"
        REVERSED = "REVERSED", "Reversed"
        DUPLICATE = "DUPLICATE", "Duplicate"
        UNRESOLVED = "UNRESOLVED", "Unresolved"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_REVIEW = "IN_REVIEW", "In review"
        RESOLVED = "RESOLVED", "Resolved"
        DISMISSED = "DISMISSED", "Dismissed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="reconciliation_items")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="reconciliation_items")
    instruction = models.ForeignKey(PaymentInstruction, on_delete=models.PROTECT, null=True, blank=True, related_name="reconciliation_items")
    issue_type = models.CharField(max_length=40, choices=IssueType.choices)
    expected_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    actual_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    expected_status = models.CharField(max_length=30, blank=True)
    actual_status = models.CharField(max_length=30, blank=True)
    provider_reference = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    resolution_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="resolved_reconciliation_items")
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=django_timezone.now)


class ReviewTask(models.Model):
    class TaskType(models.TextChoices):
        DUPLICATE_REVIEW = "DUPLICATE_REVIEW", "Duplicate review"
        RISK_REVIEW = "RISK_REVIEW", "Risk review"
        ELIGIBILITY_REVIEW = "ELIGIBILITY_REVIEW", "Eligibility review"
        PAYMENT_EXCEPTION = "PAYMENT_EXCEPTION", "Payment exception"
        RECONCILIATION = "RECONCILIATION", "Reconciliation"
        COMPLAINT_ESCALATION = "COMPLAINT_ESCALATION", "Complaint escalation"
        DATA_QUALITY = "DATA_QUALITY", "Data quality"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        ASSIGNED = "ASSIGNED", "Assigned"
        RESOLVED = "RESOLVED", "Resolved"
        DISMISSED = "DISMISSED", "Dismissed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="review_tasks")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, null=True, blank=True, related_name="review_tasks")
    task_type = models.CharField(max_length=40, choices=TaskType.choices)
    entity_type = models.CharField(max_length=120)
    entity_id = models.CharField(max_length=120)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="review_tasks")
    resolution = models.TextField(blank=True)
    created_at = models.DateTimeField(default=django_timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)


class AutomationRule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="automation_rules")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, null=True, blank=True, related_name="automation_rules")
    event_name = models.CharField(max_length=120)
    action_name = models.CharField(max_length=120)
    conditions = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    action_config = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    is_active = models.BooleanField(default=False)
    priority = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(default=django_timezone.now)


class AutomationExecution(models.Model):
    class Status(models.TextChoices):
        DRY_RUN = "DRY_RUN", "Dry run"
        BLOCKED = "BLOCKED", "Blocked"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="automation_executions")
    rule = models.ForeignKey(AutomationRule, on_delete=models.PROTECT, related_name="executions")
    idempotency_key = models.CharField(max_length=160, unique=True)
    event_payload = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    planned_action = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BLOCKED)
    created_at = models.DateTimeField(default=django_timezone.now)


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


class HouseholdEnrollment(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"
        REJECTED = "REJECTED", "Rejected"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.PROTECT, related_name="household_enrollments")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="household_enrollments")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    note = models.TextField(blank=True)
    decided_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="household_enrollment_decisions")
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=django_timezone.now)


class CashEntitlement(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        PAID = "PAID", "Paid"
        CANCELLED = "CANCELLED", "Cancelled"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.PROTECT, related_name="cash_entitlements")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="cash_entitlements")
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(0.01)])
    currency = models.CharField(max_length=3)
    conditions = models.TextField(blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_cash_entitlements")
    created_at = models.DateTimeField(default=django_timezone.now)
    updated_at = models.DateTimeField(auto_now=True)


class Warehouse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="warehouses")
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=255)
    person_in_charge = models.CharField(max_length=255, blank=True)
    responsible_team = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_warehouses")
    created_at = models.DateTimeField(default=django_timezone.now)
    updated_at = models.DateTimeField(auto_now=True)


class NFIItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="nfi_items")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="items")
    name = models.CharField(max_length=255)
    item_type = models.CharField(max_length=80, default="ITEM")
    unit = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    initial_quantity = models.PositiveIntegerField(default=0)
    available_quantity = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_nfi_items")
    created_at = models.DateTimeField(default=django_timezone.now)
    updated_at = models.DateTimeField(auto_now=True)


class NFIEntitlement(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        DISTRIBUTED = "DISTRIBUTED", "Distributed"
        CANCELLED = "CANCELLED", "Cancelled"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.PROTECT, related_name="nfi_entitlements")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="nfi_entitlements")
    item = models.ForeignKey(NFIItem, on_delete=models.PROTECT, related_name="entitlements")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="nfi_entitlements", null=True, blank=True)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    conditions = models.TextField(blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_nfi_entitlements")
    created_at = models.DateTimeField(default=django_timezone.now)
    updated_at = models.DateTimeField(auto_now=True)


class StockMovement(models.Model):
    class MovementType(models.TextChoices):
        ENTRY = "ENTRY", "Entry"
        ALLOCATION = "ALLOCATION", "Allocation"
        ISSUE = "ISSUE", "Issue"
        DISTRIBUTION = "DISTRIBUTION", "Distribution"
        RETURN = "RETURN", "Return"
        DAMAGED = "DAMAGED", "Damaged"
        LOST = "LOST", "Lost"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="stock_movements")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="stock_movements")
    item = models.ForeignKey(NFIItem, on_delete=models.PROTECT, related_name="stock_movements")
    quantity = models.PositiveIntegerField()
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    reference = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="stock_movements")
    created_at = models.DateTimeField(default=django_timezone.now)


class DistributionEvent(models.Model):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "Planned"
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"
        CANCELLED = "CANCELLED", "Cancelled"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="distribution_events")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="distribution_events", null=True, blank=True)
    location = models.CharField(max_length=255)
    event_date = models.DateField()
    distribution_team = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    evidence = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    exceptions = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_distribution_events")
    created_at = models.DateTimeField(default=django_timezone.now)


class DistributionAllocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.PROTECT, related_name="distribution_allocations")
    entitlement = models.ForeignKey(NFIEntitlement, on_delete=models.PROTECT, related_name="allocations")
    event = models.ForeignKey(DistributionEvent, on_delete=models.PROTECT, related_name="allocations")
    item = models.ForeignKey(NFIItem, on_delete=models.PROTECT, related_name="allocations")
    planned_quantity = models.PositiveIntegerField()
    allocated_quantity = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, default="PLANNED")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="distribution_allocations")
    created_at = models.DateTimeField(default=django_timezone.now)


class DistributionIssue(models.Model):
    class DeliveryStatus(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED", "Partially received"
        NOT_RECEIVED = "NOT_RECEIVED", "Not received"
        NO_SHOW = "NO_SHOW", "No show"
        REJECTED = "REJECTED", "Rejected"
        DAMAGED = "DAMAGED", "Damaged"
        LOST = "LOST", "Lost"
        RESCHEDULED = "RESCHEDULED", "Rescheduled"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.PROTECT, related_name="distribution_issues")
    entitlement = models.ForeignKey(NFIEntitlement, on_delete=models.PROTECT, related_name="issues")
    item = models.ForeignKey(NFIItem, on_delete=models.PROTECT, related_name="issues")
    event = models.ForeignKey(DistributionEvent, on_delete=models.PROTECT, related_name="issues")
    planned_quantity = models.PositiveIntegerField()
    actual_quantity = models.PositiveIntegerField(default=0)
    delivery_status = models.CharField(max_length=30, choices=DeliveryStatus.choices)
    evidence = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="distribution_issues")
    created_at = models.DateTimeField(default=django_timezone.now)
