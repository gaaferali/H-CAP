import uuid

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_pdmresponse_received_count"),
    ]

    operations = [
        migrations.AlterField(
            model_name="complaint",
            name="category",
            field=models.CharField(choices=[("PAYMENT", "Payment"), ("ACCESS", "Access"), ("ELIGIBILITY", "Eligibility")], max_length=120),
        ),
        migrations.CreateModel(
            name="ComplaintAIAnalysis",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("category", models.CharField(max_length=120)),
                ("severity", models.CharField(choices=[("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High"), ("CRITICAL", "Critical")], max_length=20)),
                ("summary", models.TextField()),
                ("possible_causes", models.JSONField(blank=True, default=list)),
                ("recommended_actions", models.JSONField(blank=True, default=list)),
                ("evidence", models.JSONField(blank=True, default=dict)),
                ("confidence", models.DecimalField(decimal_places=4, default=0, max_digits=8)),
                ("requires_escalation", models.BooleanField(default=False)),
                ("model_version", models.CharField(max_length=80)),
                ("review_decision", models.CharField(blank=True, choices=[("ACCEPT", "Accept"), ("MODIFY", "Modify"), ("DISMISS", "Dismiss")], max_length=20)),
                ("reviewer_note", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("complaint", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="ai_analysis", to="core.complaint")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reviewed_complaint_ai_analyses", to="core.user")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="complaint_ai_analyses", to="core.tenant")),
            ],
        ),
    ]
