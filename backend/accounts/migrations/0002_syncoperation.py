import uuid

import django.db.models.deletion
from django.core.serializers.json import DjangoJSONEncoder
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="SyncOperation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("operation_id", models.CharField(max_length=160)),
                ("operation_type", models.CharField(max_length=80)),
                ("client_generated_id", models.CharField(blank=True, max_length=160)),
                ("status", models.CharField(choices=[("APPLIED", "Applied"), ("DUPLICATE", "Duplicate"), ("CONFLICT", "Conflict"), ("REJECTED", "Rejected")], default="APPLIED", max_length=20)),
                ("request_hash", models.CharField(blank=True, max_length=64)),
                ("validation_results", models.JSONField(blank=True, default=dict, encoder=DjangoJSONEncoder)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(default=timezone.now)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sync_operations", to="accounts.tenant")),
            ],
            options={
                "db_table": "core_syncoperation",
                "ordering": ["-created_at"],
                "constraints": [models.UniqueConstraint(fields=("tenant", "operation_id"), name="unique_sync_operation_per_tenant")],
            },
        ),
    ]
