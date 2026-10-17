from decimal import Decimal

from django.db.models import Sum
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from django.shortcuts import get_object_or_404

from assistance.models import CashEntitlement
from payments.models import PaymentInstruction

from .models import Budget, Program


# Calculates committed, successful, used, and remaining cash budget totals.
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


PROGRAM_TRANSITIONS = {
    Program.Status.DRAFT: {Program.Status.ACTIVE},
    Program.Status.ACTIVE: {Program.Status.PAUSED, Program.Status.CLOSED},
    Program.Status.PAUSED: {Program.Status.ACTIVE, Program.Status.CLOSED},
    Program.Status.CLOSED: {Program.Status.PAUSED},
}


def require_reason(value):
    reason = str(value or "").strip()
    if len(reason) < 5:
        raise ValidationError({"reason": "Provide an audit reason of at least 5 characters."})
    return reason


def lock_program(program_id):
    return Program.objects.select_for_update().select_related("tenant").get(pk=program_id)


def require_program_active(program, operation="this operation"):
    if program.status != Program.Status.ACTIVE:
        raise ValidationError(
            {"program": f"The program must be ACTIVE for {operation}. Current status: {program.get_status_display()}."}
        )


def program_close_blockers(program):
    """Return only work that can still race with program closure."""
    from medical.models import MedicalDistributionAllocation
    from operations.models import DistributionAllocation, DistributionEvent
    from payments.models import PaymentInstruction

    blockers = {}
    payment_count = PaymentInstruction.objects.filter(
        cash_entitlement__program=program,
        status=PaymentInstruction.Status.SUBMITTED,
    ).count()
    if payment_count:
        blockers["submitted_payments"] = payment_count
    event_count = DistributionEvent.objects.filter(
        program=program,
        status__in=[DistributionEvent.Status.PLANNED, DistributionEvent.Status.OPEN],
    ).count()
    if event_count:
        blockers["open_nfi_events"] = event_count
    allocation_count = DistributionAllocation.objects.filter(
        entitlement__program=program,
        status__in=["PLANNED", "ALLOCATED"],
    ).count()
    if allocation_count:
        blockers["open_nfi_allocations"] = allocation_count
    medical_count = MedicalDistributionAllocation.objects.filter(
        entitlement__program=program,
        status=MedicalDistributionAllocation.Status.ALLOCATED,
    ).count()
    if medical_count:
        blockers["open_medical_allocations"] = medical_count
    return blockers


@transaction.atomic
# Moves a program through its controlled lifecycle and records the reason.
def transition_program(*, program, target_status, actor, reason):
    locked = lock_program(program.pk)
    reason = require_reason(reason)
    if target_status == locked.status:
        return locked, False
    if target_status not in PROGRAM_TRANSITIONS.get(locked.status, set()):
        raise ValidationError(
            {"status": f"Cannot change program from {locked.get_status_display()} to {target_status}."}
        )
    if target_status == Program.Status.CLOSED:
        blockers = program_close_blockers(locked)
        if blockers:
            raise ValidationError({"status": "Resolve in-flight operations before closing the program.", "blockers": blockers})
    locked.status = target_status
    locked.status_reason = reason
    locked.status_changed_by = actor
    locked.status_changed_at = timezone.now()
    locked.save(update_fields=["status", "status_reason", "status_changed_by", "status_changed_at"])
    return locked, True


# Returns activity progress and blockers shown in the program workspace.
def program_dependency_summary(program):
    """Conservative safe-delete check: a program is deletable only when pristine."""
    summary = {}
    for relation in program._meta.related_objects:
        accessor = relation.get_accessor_name()
        if not accessor:
            continue
        try:
            related = getattr(program, accessor)
        except ObjectDoesNotExist:
            continue
        count = related.count() if hasattr(related, "count") else int(related is not None)
        if count:
            summary[accessor] = count
    return summary
