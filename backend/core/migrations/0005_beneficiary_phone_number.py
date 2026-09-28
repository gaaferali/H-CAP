from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_programactivity_activitydependency"),
    ]

    operations = [
        migrations.AddField(
            model_name="beneficiary",
            name="phone_number",
            field=models.CharField(blank=True, max_length=30),
        ),
    ]
