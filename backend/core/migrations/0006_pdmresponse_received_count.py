from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_beneficiary_phone_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="pdmresponse",
            name="received_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
