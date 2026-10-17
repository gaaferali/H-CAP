import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone as django_timezone

from accounts.models import Tenant, User
from programs.models import Program

# PDM responses table for recipient feedback after assistance delivery.
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

    class Meta:
        db_table = "core_pdmresponse"


# Append-only audit table showing who changed data and when it happened.
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
        db_table = "core_auditevent"
        ordering = ["-created_at"]

