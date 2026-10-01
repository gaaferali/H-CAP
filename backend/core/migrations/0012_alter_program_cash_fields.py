from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [("core", "0011_alter_distributionissue_delivery_status")]

    operations = [
        migrations.AlterField(
            model_name="program",
            name="transfer_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=18,
                null=True,
                validators=[django.core.validators.MinValueValidator(0.01)],
            ),
        ),
        migrations.AlterField(
            model_name="program",
            name="payment_cycle",
            field=models.CharField(blank=True, choices=[("ONE_TIME", "One time"), ("MONTHLY", "Monthly"), ("QUARTERLY", "Quarterly")], max_length=20),
        ),
    ]
