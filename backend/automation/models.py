import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone as django_timezone

from accounts.models import Tenant, User
from programs.models import Program

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

    class Meta:
        db_table = "core_aisignal"


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

    class Meta:
        db_table = "core_reviewtask"


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

    class Meta:
        db_table = "core_automationrule"


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

    class Meta:
        db_table = "core_automationexecution"

