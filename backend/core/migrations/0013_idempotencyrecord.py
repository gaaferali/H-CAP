import uuid
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("core", "0012_alter_program_cash_fields")]

    operations = [
        migrations.CreateModel(
            name="IdempotencyRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("endpoint", models.CharField(max_length=255)),
                ("idempotency_key", models.CharField(max_length=255)),
                ("request_fingerprint", models.CharField(max_length=64)),
                ("response_status", models.PositiveSmallIntegerField(default=201)),
                ("response_body", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="idempotency_records", to="core.user")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="idempotency_records", to="core.tenant")),
            ],
        ),
        migrations.AddConstraint(
            model_name="idempotencyrecord",
            constraint=models.UniqueConstraint(fields=("tenant", "actor", "endpoint", "idempotency_key"), name="unique_idempotency_request"),
        ),
    ]
