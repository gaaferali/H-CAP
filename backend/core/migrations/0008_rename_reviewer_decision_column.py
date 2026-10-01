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
                        "DO $$ BEGIN "
                        "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'core_complaintaianalysis' AND column_name = 'reviewer_decision') "
                        "AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'core_complaintaianalysis' AND column_name = 'review_decision') THEN "
                        "ALTER TABLE core_complaintaianalysis RENAME COLUMN reviewer_decision TO review_decision; "
                        "END IF; END $$;"
                    ),
                    reverse_sql=(
                        "DO $$ BEGIN "
                        "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'core_complaintaianalysis' AND column_name = 'review_decision') "
                        "AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'core_complaintaianalysis' AND column_name = 'reviewer_decision') THEN "
                        "ALTER TABLE core_complaintaianalysis RENAME COLUMN review_decision TO reviewer_decision; "
                        "END IF; END $$;"
                    ),
                ),
            ],
            state_operations=[],
        ),
    ]
