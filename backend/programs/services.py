from decimal import Decimal

from django.db.models import Sum
from django.shortcuts import get_object_or_404

from assistance.models import CashEntitlement
from payments.models import PaymentInstruction

from .models import Budget, Program


def payment_budget_totals(program):
    """Committed entitlements reserve funding; only successful payments consume fees."""
    committed = CashEntitlement.objects.filter(
        program=program,
        status__in=[CashEntitlement.Status.ACTIVE, CashEntitlement.Status.PAID],
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    successful = PaymentInstruction.objects.filter(
        cash_entitlement__program=program,
        status=PaymentInstruction.Status.SUCCESS,
    )
    funding = successful.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    fees = successful.aggregate(total=Sum("applied_fee"))["total"] or Decimal("0")
    budget = Budget.objects.filter(program=program).first()
    budget_amount = budget.planned_total if budget else Decimal("0")
    used = committed + fees
    return {
        "budget": budget,
        "budget_amount": budget_amount,
        "committed": committed,
        "funding": funding,
        "fees": fees,
        "used": used,
        "remaining": budget_amount - used,
    }


def scoped_program(request, program_id):
    programs = Program.objects.all() if request.user.is_superuser else Program.objects.filter(tenant=request.user.tenant)
    return get_object_or_404(programs, id=program_id)
