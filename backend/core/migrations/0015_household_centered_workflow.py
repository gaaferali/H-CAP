from django.db import migrations, models
import django.db.models.deletion


def backfill_household_links(apps, schema_editor):
    Enrollment = apps.get_model("core", "Enrollment")
    CashEntitlement = apps.get_model("core", "CashEntitlement")
    NFIEntitlement = apps.get_model("core", "NFIEntitlement")
    DistributionAllocation = apps.get_model("core", "DistributionAllocation")
    DistributionIssue = apps.get_model("core", "DistributionIssue")
    for model in (Enrollment, CashEntitlement, NFIEntitlement, DistributionAllocation, DistributionIssue):
        for record in model.objects.filter(household__isnull=True).select_related("beneficiary__household"):
            if record.beneficiary_id:
                record.household_id = record.beneficiary.household_id
                record.save(update_fields=["household"])


class Migration(migrations.Migration):
    dependencies = [("core", "0014_alter_aisignal_evidence_alter_auditevent_after_and_more")]

    operations = [
        migrations.RemoveConstraint(model_name="household", name="unique_household_client_id_per_tenant"),
        # Drop the legacy constraint before renaming the field.  Django renders
        # the constraint condition against the migration state; doing this
        # after RenameField makes the old ``client_generated_id`` condition
        # impossible to resolve during migrate.
        migrations.RenameField(model_name="household", old_name="client_generated_id", new_name="registration_reference"),
        migrations.AddConstraint(
            model_name="household",
            constraint=models.UniqueConstraint(
                condition=~models.Q(registration_reference=""),
                fields=("tenant", "registration_reference"),
                name="unique_household_registration_reference_per_tenant",
            ),
        ),
        migrations.RemoveField(model_name="beneficiary", name="phone_last4"),
        migrations.AddField(model_name="enrollment", name="household", field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="legacy_enrollments", to="core.household")),
        migrations.AddField(model_name="cashentitlement", name="household", field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="cash_entitlements", to="core.household")),
        migrations.AddField(model_name="nfientitlement", name="household", field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="nfi_entitlements", to="core.household")),
        migrations.AddField(model_name="distributionallocation", name="household", field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="distribution_allocations", to="core.household")),
        migrations.AddField(model_name="distributionissue", name="household", field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="distribution_issues", to="core.household")),
        migrations.AddField(model_name="householdenrollment", name="assistance_modality", field=models.CharField(choices=[("CASH", "Payment"), ("NFI", "NFI"), ("CASH_NFI", "Payment + NFI")], default="CASH", max_length=12)),
        migrations.AlterField(model_name="enrollment", name="beneficiary", field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="enrollments", to="core.beneficiary")),
        migrations.AlterField(model_name="cashentitlement", name="beneficiary", field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="cash_entitlements", to="core.beneficiary")),
        migrations.AlterField(model_name="nfientitlement", name="beneficiary", field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="nfi_entitlements", to="core.beneficiary")),
        migrations.AlterField(model_name="distributionallocation", name="beneficiary", field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="distribution_allocations", to="core.beneficiary")),
        migrations.AlterField(model_name="distributionissue", name="beneficiary", field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="distribution_issues", to="core.beneficiary")),
        migrations.RunPython(backfill_household_links, migrations.RunPython.noop),
    ]
