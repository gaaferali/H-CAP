from types import SimpleNamespace

from rest_framework.exceptions import ValidationError

from automation.ai_source import validate_task3_cash_amount


def validate_cash_amount_or_raise(*, program, household, amount, entity_type, entity_id):
    """Run the protected CASH-R1 validator before the caller opens its write transaction."""
    beneficiary = SimpleNamespace(
        id=household.id,
        household=household,
        household_id=household.id,
    )
    result = validate_task3_cash_amount(
        program=program,
        beneficiary=beneficiary,
        proposed_amount=amount,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    if not result.get("valid", True):
        raise ValidationError({
            "amount": result.get("reason", "The cash amount requires human review."),
            "anomaly_signal": result.get("signal_id"),
            "review_task": result.get("review_task_id"),
        })
    return result
