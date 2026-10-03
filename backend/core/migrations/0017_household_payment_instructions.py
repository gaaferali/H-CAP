from django.db import migrations, models
from django.db.models import Q
import django.db.models.deletion


def backfill_cash_entitlements(apps, schema_editor):
    PaymentInstruction = apps.get_model("core", "PaymentInstruction")
    CashEntitlement = apps.get_model("core", "CashEntitlement")

    candidates_by_instruction = {}
    instruction_counts_by_entitlement = {}
    instructions = PaymentInstruction.objects.select_related(
        "enrollment__household",
        "enrollment__beneficiary__household",
        "beneficiary__household",
    )
    for instruction in instructions.filter(cash_entitlement__isnull=True):
        enrollment = instruction.enrollment
        beneficiary = instruction.beneficiary
        if enrollment:
            program_id = enrollment.program_id
            household_id = enrollment.household_id or (
                enrollment.beneficiary.household_id if enrollment.beneficiary_id else None
            )
        elif beneficiary:
            program_id = beneficiary.household.program_id
            household_id = beneficiary.household_id
        else:
            continue
        if not household_id or not program_id:
            continue
        if enrollment and enrollment.household_id and enrollment.beneficiary_id:
            if enrollment.household_id != enrollment.beneficiary.household_id:
                continue
        matches = list(
            CashEntitlement.objects.filter(
                household_id=household_id,
                program_id=program_id,
                amount=instruction.amount,
                currency=instruction.currency,
            )
            .filter(Q(beneficiary_id=instruction.beneficiary_id) | Q(beneficiary__isnull=True))
            .values_list("id", flat=True)[:2]
        )
        if len(matches) == 1:
            entitlement_id = matches[0]
            candidates_by_instruction[instruction.pk] = entitlement_id
            instruction_counts_by_entitlement[entitlement_id] = instruction_counts_by_entitlement.get(entitlement_id, 0) + 1

    for instruction_id, entitlement_id in candidates_by_instruction.items():
        if instruction_counts_by_entitlement[entitlement_id] == 1:
            PaymentInstruction.objects.filter(pk=instruction_id).update(cash_entitlement_id=entitlement_id)


class Migration(migrations.Migration):
    dependencies = [("core", "0016_alter_householdenrollment_status")]

    operations = [
        migrations.AddField(
            model_name="paymentinstruction",
            name="cash_entitlement",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payment_instructions",
                to="core.cashentitlement",
            ),
        ),
        migrations.AlterField(
            model_name="paymentinstruction",
            name="enrollment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payment_instructions",
                to="core.enrollment",
            ),
        ),
        migrations.AlterField(
            model_name="paymentinstruction",
            name="beneficiary",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payment_instructions",
                to="core.beneficiary",
            ),
        ),
        migrations.RunPython(backfill_cash_entitlements, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="paymentinstruction",
            constraint=models.UniqueConstraint(
                condition=models.Q(cash_entitlement__isnull=False),
                fields=("cash_entitlement",),
                name="unique_payment_instruction_per_cash_entitlement",
            ),
        ),
    ]
