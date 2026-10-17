import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone as django_timezone

from accounts.models import Tenant, User

# Programs table with the enabled assistance types and lifecycle status.
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
    status_reason = models.TextField(blank=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_changed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="program_status_changes",
    )
    workflow_config = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    country_pack = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_programs")
    created_at = models.DateTimeField(default=django_timezone.now)
    cash_enabled = models.BooleanField(default=True)
    nfi_enabled = models.BooleanField(default=False)
    medical_enabled = models.BooleanField(default=False)
    other_assistance_enabled = models.BooleanField(default=False)

    class Meta:
        db_table = "core_program"


# Program activities table used to plan and follow operational work.
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
        db_table = "core_programactivity"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(progress__gte=0) & models.Q(progress__lte=100),
                name="program_activity_progress_range",
            ),
        ]


# Links activities that must be completed in a specific order.
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
        db_table = "core_activitydependency"
        constraints = [models.UniqueConstraint(fields=["predecessor", "successor"], name="unique_activity_dependency")]


# Stores the payment budget configured for a cash-enabled program.
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

    class Meta:
        db_table = "core_budget"

