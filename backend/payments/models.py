import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone as django_timezone

from accounts.models import Tenant, User
from assistance.models import Beneficiary, CashEntitlement, Enrollment
from programs.models import Program

# Payment channel settings for simulated program transfers.
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
    fee_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "core_paymentchannelconfig"


# Payment instructions table with the amount, beneficiary, and delivery status.
class PaymentInstruction(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        CREATED = "CREATED", "Created"
        SUBMITTED = "SUBMITTED", "Submitted"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        REVERSED = "REVERSED", "Reversed"
        CANCELLED = "CANCELLED", "Cancelled before submission"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey("PaymentBatch", on_delete=models.PROTECT, null=True, blank=True, related_name="instructions")
    cash_entitlement = models.ForeignKey("assistance.CashEntitlement", on_delete=models.PROTECT, null=True, blank=True, related_name="payment_instructions")
    enrollment = models.ForeignKey(Enrollment, on_delete=models.PROTECT, null=True, blank=True, related_name="payment_instructions")
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.PROTECT, null=True, blank=True, related_name="payment_instructions")
    channel_config = models.ForeignKey(PaymentChannelConfig, on_delete=models.PROTECT, related_name="payment_instructions")
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(0.01)])
    applied_fee = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    provider_reference = models.CharField(max_length=120, blank=True)
    idempotency_key = models.CharField(max_length=160, unique=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_payment_instructions")
    created_at = models.DateTimeField(default=django_timezone.now)

    class Meta:
        db_table = "core_paymentinstruction"
        constraints = [
            models.UniqueConstraint(
                fields=["cash_entitlement"],
                condition=models.Q(cash_entitlement__isnull=False),
                name="unique_payment_instruction_per_cash_entitlement",
            )
        ]


# Append-only payment history used to trace every payment status change.
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
        CANCELLED = "CANCELLED", "Cancelled before submission"

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

    class Meta:
        db_table = "core_paymentevent"


# Groups payment instructions that are prepared and processed together.
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

    class Meta:
        db_table = "core_paymentbatch"


# Records payment differences that finance users need to review.
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

    class Meta:
        db_table = "core_reconciliationitem"

