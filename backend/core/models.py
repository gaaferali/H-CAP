import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import MinValueValidator
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
    transfer_amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(0.01)])
    payment_cycle = models.CharField(max_length=20, choices=Cycle.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    workflow_config = models.JSONField(default=dict, blank=True)
    country_pack = models.JSONField(default=dict, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_programs")
    created_at = models.DateTimeField(default=django_timezone.now)


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
    redacted_payload = models.JSONField(default=dict, blank=True)
    recorded_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="payment_events")
    created_at = models.DateTimeField(default=django_timezone.now)


class Complaint(models.Model):
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
    category = models.CharField(max_length=120)
    description = models.TextField()
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="assigned_complaints")
    resolution_notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_complaints")
    created_at = models.DateTimeField(default=django_timezone.now)


class ComplaintAIAnalysis(models.Model):
    """Explainable AI-assisted analysis of a complaint. Human review remains authoritative."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    complaint = models.OneToOneField(Complaint, on_delete=models.CASCADE, related_name="ai_analysis")
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="complaint_ai_analyses")
    category = models.CharField(max_length=120)
    severity = models.CharField(max_length=20)
    summary = models.TextField()
    possible_causes = models.JSONField(default=list, blank=True)
    recommended_actions = models.JSONField(default=list, blank=True)
    evidence = models.JSONField(default=dict, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    model_version = models.CharField(max_length=80, default="complaint-rules-v1")
    requires_escalation = models.BooleanField(default=False)
    reviewer_decision = models.CharField(max_length=30, blank=True)
    reviewer_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="reviewed_complaint_ai_analyses")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=django_timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["complaint", "tenant"], name="unique_complaint_ai_analysis_per_tenant")
        ]


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
    line_items = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=django_timezone.now)


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="audit_events")
    actor = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="audit_events")
    action = models.CharField(max_length=120)
    entity_type = models.CharField(max_length=120)
    entity_id = models.CharField(max_length=120)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
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
    evidence = models.JSONField(default=dict, blank=True)
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
    provider_response = models.JSONField(default=dict, blank=True)
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


class SyncOperation(models.Model):
    class Status(models.TextChoices):
        APPLIED = "APPLIED", "Applied"
        DUPLICATE = "DUPLICATE", "Duplicate"
        CONFLICT = "CONFLICT", "Conflict"
        REJECTED = "REJECTED", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="sync_operations")
    operation_id = models.CharField(max_length=160)
    operation_type = models.CharField(max_length=80)
    client_generated_id = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPLIED)
    request_hash = models.CharField(max_length=64, blank=True)
    validation_results = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(default=django_timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["tenant", "operation_id"], name="unique_sync_operation_per_tenant")]



class AutomationRule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="automation_rules")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, null=True, blank=True, related_name="automation_rules")
    event_name = models.CharField(max_length=120)
    action_name = models.CharField(max_length=120)
    conditions = models.JSONField(default=dict, blank=True)
    action_config = models.JSONField(default=dict, blank=True)
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
    event_payload = models.JSONField(default=dict, blank=True)
    planned_action = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BLOCKED)
    created_at = models.DateTimeField(default=django_timezone.now)
