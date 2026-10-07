import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0017_household_payment_instructions")]

    operations = [
        migrations.AddField(
            model_name="paymentchannelconfig",
            name="fee_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=18,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AddField(
            model_name="paymentinstruction",
            name="applied_fee",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=18,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.CreateModel(
            name="PaymentDeliveryReview",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("delivery_status", models.CharField(choices=[("RECEIVED", "Received"), ("PARTIALLY_RECEIVED", "Partially received"), ("NOT_RECEIVED", "Not received"), ("NO_SHOW", "No show"), ("REJECTED", "Rejected"), ("RESCHEDULED", "Rescheduled"), ("EXCEPTION", "Exception")], max_length=30)),
                ("note", models.TextField(blank=True)),
                ("evidence", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("household", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payment_delivery_reviews", to="core.household")),
                ("payment_instruction", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="delivery_review", to="core.paymentinstruction")),
                ("program", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payment_delivery_reviews", to="core.program")),
                ("reviewed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payment_delivery_reviews", to="core.user")),
            ],
        ),
    ]
