import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone as django_timezone

from accounts.models import Tenant, User
from assistance.models import Beneficiary, Household
from payments.models import PaymentInstruction
from programs.models import Program

# Stores reviewer decisions about completed or failed payment delivery.
class PaymentDeliveryReview(models.Model):
    class DeliveryStatus(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED", "Partially received"
        NOT_RECEIVED = "NOT_RECEIVED", "Not received"
        NO_SHOW = "NO_SHOW", "No show"
        REJECTED = "REJECTED", "Rejected"
        RESCHEDULED = "RESCHEDULED", "Rescheduled"
        EXCEPTION = "EXCEPTION", "Exception"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment_instruction = models.OneToOneField(
        PaymentInstruction,
        on_delete=models.PROTECT,
        related_name="delivery_review",
    )
    household = models.ForeignKey(Household, on_delete=models.PROTECT, related_name="payment_delivery_reviews")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="payment_delivery_reviews")
    delivery_status = models.CharField(max_length=30, choices=DeliveryStatus.choices)
    note = models.TextField(blank=True)
    evidence = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="payment_delivery_reviews")
    reviewed_at = models.DateTimeField(default=django_timezone.now)
    created_at = models.DateTimeField(default=django_timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "core_paymentdeliveryreview"


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

    class Meta:
        db_table = "core_complaint"


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

    class Meta:
        db_table = "core_complaintaianalysis"


# Warehouses table for program stock and distribution locations.
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

    class Meta:
        db_table = "core_warehouse"


# NFI items table with the unit and minimum stock level.
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

    class Meta:
        db_table = "core_nfiitem"


# NFI entitlements table for approved beneficiary items and quantities.
class NFIEntitlement(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        DISTRIBUTED = "DISTRIBUTED", "Distributed"
        CANCELLED = "CANCELLED", "Cancelled"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.PROTECT, related_name="nfi_entitlements", null=True, blank=True)
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.PROTECT, related_name="nfi_entitlements", null=True, blank=True)
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

    class Meta:
        db_table = "core_nfientitlement"


# Append-only NFI stock history for receipts, reservations, issues, and adjustments.
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

    class Meta:
        db_table = "core_stockmovement"


# Distribution events table for planned NFI or medical delivery sessions.
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

    class Meta:
        db_table = "core_distributionevent"


# Connects an approved entitlement to its distribution event.
class DistributionAllocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.PROTECT, related_name="distribution_allocations", null=True, blank=True)
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.PROTECT, related_name="distribution_allocations", null=True, blank=True)
    entitlement = models.ForeignKey(NFIEntitlement, on_delete=models.PROTECT, related_name="allocations")
    event = models.ForeignKey(DistributionEvent, on_delete=models.PROTECT, related_name="allocations")
    item = models.ForeignKey(NFIItem, on_delete=models.PROTECT, related_name="allocations")
    planned_quantity = models.PositiveIntegerField()
    allocated_quantity = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, default="PLANNED")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="distribution_allocations")
    created_at = models.DateTimeField(default=django_timezone.now)

    class Meta:
        db_table = "core_distributionallocation"


# Stores the final delivery result and evidence for an allocation.
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
    household = models.ForeignKey(Household, on_delete=models.PROTECT, related_name="distribution_issues", null=True, blank=True)
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.PROTECT, related_name="distribution_issues", null=True, blank=True)
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

    class Meta:
        db_table = "core_distributionissue"

