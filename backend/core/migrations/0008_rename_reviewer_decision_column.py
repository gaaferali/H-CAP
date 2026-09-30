from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_complaint_category_and_ai_analysis"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE core_complaintaianalysis "
                        "RENAME COLUMN reviewer_decision TO review_decision;"
                    ),
                    reverse_sql=(
                        "ALTER TABLE core_complaintaianalysis "
                        "RENAME COLUMN review_decision TO reviewer_decision;"
                    ),
                ),
            ],
            state_operations=[],
        ),
    ]
