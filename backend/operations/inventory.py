from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from .models import DistributionIssue, NFIEntitlement, StockMovement

def _nfi_reserved_quantity(item, exclude_entitlement_id=None):
    entitlements = list(
        NFIEntitlement.objects.filter(
            item=item,
            status__in=[NFIEntitlement.Status.ACTIVE, NFIEntitlement.Status.DISTRIBUTED],
        ).exclude(pk=exclude_entitlement_id).prefetch_related("issues")
    )
    return sum(
        max(entitlement.quantity - sum(issue.actual_quantity for issue in entitlement.issues.all()), 0)
        for entitlement in entitlements
    )


def _available_nfi_stock(item, exclude_entitlement_id=None):
    movements = list(
        StockMovement.objects.filter(item=item).values("movement_type", "quantity", "reference")
    )
    entitlements = list(NFIEntitlement.objects.filter(item=item).only("id", "quantity", "status"))
    entitlement_ids = {str(entitlement.id) for entitlement in entitlements}
    initial_entry = sum(
        movement["quantity"]
        for movement in movements
        if movement["movement_type"] == StockMovement.MovementType.ENTRY
        and movement["reference"] == "initial stock"
    )
    other_entries = sum(
        movement["quantity"]
        for movement in movements
        if movement["movement_type"] == StockMovement.MovementType.ENTRY
        and movement["reference"] != "initial stock"
    )
    returned_stock = sum(
        movement["quantity"]
        for movement in movements
        if movement["movement_type"] == StockMovement.MovementType.RETURN
        and movement["reference"] not in entitlement_ids
    )
    distribution_movements = sum(
        movement["quantity"]
        for movement in movements
        if movement["movement_type"] == StockMovement.MovementType.DISTRIBUTION
    )
    delivered_issues = DistributionIssue.objects.filter(item=item).aggregate(
        total=Sum("actual_quantity")
    )["total"]
    delivered = delivered_issues if delivered_issues is not None else distribution_movements
    unlinked_allocations = sum(
        movement["quantity"]
        for movement in movements
        if movement["movement_type"] == StockMovement.MovementType.ALLOCATION
        and movement["reference"] not in entitlement_ids
    )
    other_outflows = sum(
        movement["quantity"]
        for movement in movements
        if movement["movement_type"]
        in {
            StockMovement.MovementType.ISSUE,
            StockMovement.MovementType.DAMAGED,
            StockMovement.MovementType.LOST,
        }
    )
    baseline = initial_entry or max(item.initial_quantity, item.available_quantity)
    free_stock = (
        baseline
        + other_entries
        + returned_stock
        - delivered
        - unlinked_allocations
        - other_outflows
        - _nfi_reserved_quantity(item, exclude_entitlement_id)
    )
    excluded_reservation = 0
    if exclude_entitlement_id:
        current = NFIEntitlement.objects.filter(pk=exclude_entitlement_id, item=item).first()
        if current and current.status != NFIEntitlement.Status.CANCELLED:
            delivered = current.issues.aggregate(total=Sum("actual_quantity"))["total"] or 0
            excluded_reservation = max(current.quantity - delivered, 0)
    free_stock = min(free_stock, item.available_quantity + excluded_reservation)
    return max(free_stock, 0)


def _reserve_nfi_entitlement_stock(entitlement, item, actor):
    delivered = entitlement.issues.aggregate(total=Sum("actual_quantity"))["total"] or 0
    outstanding = max(entitlement.quantity - delivered, 0)
    movements = StockMovement.objects.filter(item=item, reference=str(entitlement.pk))
    already_reserved = (
        movements.filter(movement_type=StockMovement.MovementType.ALLOCATION).aggregate(
            total=Sum("quantity")
        )["total"]
        or 0
    ) - (
        movements.filter(movement_type=StockMovement.MovementType.RETURN).aggregate(
            total=Sum("quantity")
        )["total"]
        or 0
    )
    unreserved = max(outstanding - max(already_reserved - delivered, 0), 0)
    if unreserved > _available_nfi_stock(item, exclude_entitlement_id=entitlement.pk):
        raise ValidationError(
            {
                "quantity": (
                    f"Insufficient stock for {item.name}. Available: "
                    f"{_available_nfi_stock(item, exclude_entitlement_id=entitlement.pk)}; "
                    f"requested: {unreserved}."
                )
            }
        )
    if unreserved:
        item.available_quantity -= unreserved
        item.save(update_fields=["available_quantity", "updated_at"])
        StockMovement.objects.create(
            program=entitlement.program,
            warehouse=entitlement.warehouse,
            item=item,
            quantity=unreserved,
            movement_type=StockMovement.MovementType.ALLOCATION,
            reference=str(entitlement.pk),
            created_by=actor,
        )

