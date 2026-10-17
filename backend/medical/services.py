from datetime import timedelta
import uuid

from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import (
    ControlledProductAuthorization,
    MedicalAlert,
    MedicalBatch,
    MedicalDistributionAllocation,
    MedicalDistributionIssue,
    MedicalEntitlement,
    MedicalStockMovement,
    TemperatureObservation,
)


def _authorized_for_controlled(program, user, permission):
    today = timezone.localdate()
    return ControlledProductAuthorization.objects.filter(
        program=program,
        user=user,
        active=True,
        valid_from__lte=today,
        **{permission: True},
    ).filter(valid_until__isnull=True).exists() or ControlledProductAuthorization.objects.filter(
        program=program,
        user=user,
        active=True,
        valid_from__lte=today,
        valid_until__gte=today,
        **{permission: True},
    ).exists()


def require_controlled_authorization(program, product, user, permission):
    if product.controlled and not _authorized_for_controlled(program, user, permission):
        raise PermissionDenied("An active controlled-product authorization is required for this operation.")


# Receives a medical batch and records the opening stock movement.
def receive_batch(serializer, actor):
    data = serializer.validated_data
    require_controlled_authorization(data["program"], data["product"], actor, "can_receive")
    with transaction.atomic():
        batch = serializer.save(available_quantity=data["received_quantity"], created_by=actor)
        MedicalStockMovement.objects.create(
            program=batch.program,
            product=batch.product,
            batch=batch,
            destination_warehouse=batch.warehouse,
            quantity=batch.received_quantity,
            movement_type=MedicalStockMovement.MovementType.RECEIPT,
            reason="Batch received",
            reference=str(batch.pk),
            responsible_user=actor,
        )
        return batch


# Allocates approved medical stock using the earliest-expiry batch first.
def allocate_entitlement(entitlement_id, actor, batch_id=None):
    with transaction.atomic():
        entitlement = MedicalEntitlement.objects.select_for_update().select_related("program", "product", "warehouse").get(pk=entitlement_id)
        if entitlement.status != MedicalEntitlement.Status.APPROVED:
            raise ValidationError({"status": "Only an approved medical entitlement can be allocated."})
        require_controlled_authorization(entitlement.program, entitlement.product, actor, "can_issue")
        candidates = MedicalBatch.objects.select_for_update().filter(
            product=entitlement.product,
            warehouse=entitlement.warehouse,
            program=entitlement.program,
            status=MedicalBatch.Status.AVAILABLE,
            available_quantity__gte=entitlement.quantity,
        )
        today = timezone.localdate()
        candidates = candidates.filter(Q(expiry_date__isnull=True) | Q(expiry_date__gt=today)).order_by(
            F("expiry_date").asc(nulls_last=True), "received_date", "created_at"
        )
        if batch_id:
            candidates = candidates.filter(pk=batch_id)
        batch = candidates.first()
        if not batch:
            raise ValidationError({"batch": "No valid non-expired batch has enough available stock."})
        batch.available_quantity -= entitlement.quantity
        batch.save(update_fields=["available_quantity", "updated_at"])
        entitlement.batch = batch
        entitlement.status = MedicalEntitlement.Status.ALLOCATED
        entitlement.save(update_fields=["batch", "status", "updated_at"])
        MedicalStockMovement.objects.create(
            program=entitlement.program,
            product=entitlement.product,
            batch=batch,
            source_warehouse=entitlement.warehouse,
            quantity=entitlement.quantity,
            movement_type=MedicalStockMovement.MovementType.ALLOCATION,
            reason="Entitlement allocation using FEFO",
            reference=str(entitlement.pk),
            responsible_user=actor,
        )
        return entitlement


def cancel_distribution_allocation(allocation_id, actor, reason):
    with transaction.atomic():
        allocation = MedicalDistributionAllocation.objects.select_for_update().select_related(
            "entitlement__program"
        ).get(pk=allocation_id)
        if allocation.status == MedicalDistributionAllocation.Status.CANCELLED:
            return allocation
        if allocation.issues.exists() or allocation.status == MedicalDistributionAllocation.Status.DISTRIBUTED:
            raise ValidationError({"status": "A medical issue exists; this allocation is immutable."})
        allocation.status = MedicalDistributionAllocation.Status.CANCELLED
        allocation.save(update_fields=["status"])
        return allocation


def cancel_entitlement(entitlement_id, actor, reason=""):
    with transaction.atomic():
        entitlement = MedicalEntitlement.objects.select_for_update().get(pk=entitlement_id)
        if entitlement.status == MedicalEntitlement.Status.DISTRIBUTED:
            raise ValidationError({"status": "A distributed entitlement cannot be cancelled."})
        active_allocations = entitlement.distribution_allocations.exclude(status=MedicalDistributionAllocation.Status.CANCELLED)
        if active_allocations.filter(Q(issues__isnull=False) | Q(status=MedicalDistributionAllocation.Status.DISTRIBUTED)).exists():
            raise ValidationError({"status": "A medical issue exists; preserve the entitlement and record a delivery correction."})
        active_allocations.update(status=MedicalDistributionAllocation.Status.CANCELLED)
        if entitlement.status == MedicalEntitlement.Status.ALLOCATED and entitlement.batch_id:
            batch = MedicalBatch.objects.select_for_update().get(pk=entitlement.batch_id)
            batch.available_quantity += entitlement.quantity
            batch.save(update_fields=["available_quantity", "updated_at"])
            MedicalStockMovement.objects.create(
                program=entitlement.program, product=entitlement.product, batch=batch,
                destination_warehouse=entitlement.warehouse, quantity=entitlement.quantity,
                movement_type=MedicalStockMovement.MovementType.RETURN, reason=reason or "Entitlement cancelled",
                reference=str(entitlement.pk), responsible_user=actor,
            )
        entitlement.status = MedicalEntitlement.Status.CANCELLED
        entitlement.save(update_fields=["status", "updated_at"])
        return entitlement


def create_distribution_allocation(serializer, actor):
    data = serializer.validated_data
    entitlement, event, batch = data["entitlement"], data["event"], data["batch"]
    if entitlement.status != MedicalEntitlement.Status.ALLOCATED or entitlement.batch_id != batch.id:
        raise ValidationError("Entitlement must already be allocated to the selected batch.")
    if event.program_id != entitlement.program_id or event.warehouse_id != entitlement.warehouse_id:
        raise ValidationError("Distribution event must match the entitlement program and warehouse.")
    allocated = entitlement.distribution_allocations.exclude(status=MedicalDistributionAllocation.Status.CANCELLED).aggregate(total=Sum("quantity"))["total"] or 0
    if allocated + data["quantity"] > entitlement.quantity:
        raise ValidationError({"quantity": "Allocation exceeds the entitlement quantity."})
    return serializer.save(created_by=actor)


# Records recipient delivery and reduces the allocated batch quantity.
def issue_distribution(serializer, actor):
    data = serializer.validated_data
    with transaction.atomic():
        allocation = MedicalDistributionAllocation.objects.select_for_update().select_related(
            "entitlement__program", "entitlement__product", "entitlement__warehouse", "batch"
        ).get(pk=data["allocation"].pk)
        require_controlled_authorization(allocation.entitlement.program, allocation.entitlement.product, actor, "can_issue")
        previously_issued = allocation.issues.aggregate(total=Sum("actual_quantity"))["total"] or 0
        if previously_issued + data["actual_quantity"] > allocation.quantity:
            raise ValidationError({"actual_quantity": "Issued quantity exceeds the allocated quantity."})
        issue = serializer.save(allocation=allocation, created_by=actor)
        if data["actual_quantity"]:
            MedicalStockMovement.objects.create(
                program=allocation.entitlement.program, product=allocation.entitlement.product,
                batch=allocation.batch, source_warehouse=allocation.entitlement.warehouse,
                quantity=data["actual_quantity"], movement_type=MedicalStockMovement.MovementType.DISTRIBUTION,
                reason=issue.get_delivery_status_display(), reference=str(issue.pk), responsible_user=actor,
            )
        total = previously_issued + data["actual_quantity"]
        if total >= allocation.quantity:
            allocation.status = MedicalDistributionAllocation.Status.DISTRIBUTED
            allocation.save(update_fields=["status"])
        entitlement = allocation.entitlement
        distributed = entitlement.distribution_allocations.filter(status=MedicalDistributionAllocation.Status.DISTRIBUTED).aggregate(total=Sum("quantity"))["total"] or 0
        if distributed >= entitlement.quantity:
            entitlement.status = MedicalEntitlement.Status.DISTRIBUTED
            entitlement.save(update_fields=["status", "updated_at"])
        return issue


def adjust_batch(batch_id, quantity, direction, reason, actor):
    if quantity < 1 or direction not in {"IN", "OUT"}:
        raise ValidationError("Direction must be IN or OUT and quantity must be positive.")
    with transaction.atomic():
        batch = MedicalBatch.objects.select_for_update().get(pk=batch_id)
        require_controlled_authorization(batch.program, batch.product, actor, "can_issue" if direction == "OUT" else "can_receive")
        if direction == "OUT" and quantity > batch.available_quantity:
            raise ValidationError({"quantity": "Adjustment exceeds available stock."})
        batch.available_quantity += quantity if direction == "IN" else -quantity
        batch.save(update_fields=["available_quantity", "updated_at"])
        MedicalStockMovement.objects.create(
            program=batch.program, product=batch.product, batch=batch,
            source_warehouse=batch.warehouse if direction == "OUT" else None,
            destination_warehouse=batch.warehouse if direction == "IN" else None,
            quantity=quantity,
            movement_type=MedicalStockMovement.MovementType.ADJUSTMENT_IN if direction == "IN" else MedicalStockMovement.MovementType.ADJUSTMENT_OUT,
            reason=reason, reference=str(batch.pk), responsible_user=actor,
        )
        return batch


# Transfers medical stock between warehouses with matching audit movements.
def transfer_batch(batch_id, destination_warehouse, quantity, reason, actor):
    if quantity < 1:
        raise ValidationError({"quantity": "Transfer quantity must be positive."})
    with transaction.atomic():
        source = MedicalBatch.objects.select_for_update().select_related("program", "product", "warehouse").get(pk=batch_id)
        destination = MedicalBatch.objects.select_for_update().filter(
            product=source.product, warehouse=destination_warehouse, lot_number=source.lot_number,
        ).first()
        if destination_warehouse.program_id != source.program_id or destination_warehouse.id == source.warehouse_id:
            raise ValidationError({"destination_warehouse": "Choose another warehouse in the same program."})
        if source.status != MedicalBatch.Status.AVAILABLE or source.available_quantity < quantity:
            raise ValidationError({"quantity": "Transfer exceeds available stock or the source batch is unavailable."})
        require_controlled_authorization(source.program, source.product, actor, "can_issue")
        source.available_quantity -= quantity
        source.save(update_fields=["available_quantity", "updated_at"])
        if destination:
            if destination.status != MedicalBatch.Status.AVAILABLE:
                raise ValidationError({"destination_warehouse": "The matching destination batch is not available."})
            destination.received_quantity += quantity
            destination.available_quantity += quantity
            destination.save(update_fields=["received_quantity", "available_quantity", "updated_at"])
        else:
            destination = MedicalBatch.objects.create(
                program=source.program, product=source.product, warehouse=destination_warehouse,
                lot_number=source.lot_number, manufacturing_date=source.manufacturing_date,
                expiry_date=source.expiry_date, received_quantity=quantity, available_quantity=quantity,
                supplier_source=f"Transfer from {source.warehouse.name}", received_date=timezone.localdate(),
                storage_location=source.storage_location,
                intended_distribution_location=source.intended_distribution_location,
                status=MedicalBatch.Status.AVAILABLE, notes=reason, created_by=actor,
            )
        reference = f"TRANSFER-{uuid.uuid4()}"
        for movement_type, batch, source_warehouse, destination_location in (
            (MedicalStockMovement.MovementType.TRANSFER_OUT, source, source.warehouse, destination_warehouse),
            (MedicalStockMovement.MovementType.TRANSFER_IN, destination, source.warehouse, destination_warehouse),
        ):
            MedicalStockMovement.objects.create(
                program=source.program, product=source.product, batch=batch,
                source_warehouse=source_warehouse, destination_warehouse=destination_location,
                quantity=quantity, movement_type=movement_type, reason=reason,
                reference=reference, responsible_user=actor,
            )
        return source, destination


# Saves a cold-chain reading and quarantines unsafe stock when required.
def record_temperature(serializer, actor):
    batch = serializer.validated_data["batch"]
    product = batch.product
    temperature = serializer.validated_data["temperature"]
    excursion = bool(product.refrigeration_required and (
        product.minimum_temperature is None or product.maximum_temperature is None
        or temperature < product.minimum_temperature or temperature > product.maximum_temperature
    ))
    quarantine = bool(serializer.validated_data.get("quarantine_decision", False))
    with transaction.atomic():
        observation = serializer.save(excursion=excursion, responsible_user=actor)
        if excursion:
            MedicalAlert.objects.get_or_create(
                program=batch.program, product=product, batch=batch, warehouse=batch.warehouse,
                alert_type=MedicalAlert.AlertType.COLD_CHAIN, status=MedicalAlert.Status.OPEN,
                defaults={"message": f"Temperature excursion recorded at {temperature}."},
            )
            if quarantine:
                batch.status = MedicalBatch.Status.QUARANTINED
                batch.save(update_fields=["status", "updated_at"])
                MedicalStockMovement.objects.create(
                    program=batch.program, product=product, batch=batch, source_warehouse=batch.warehouse,
                    quantity=max(batch.available_quantity, 1), movement_type=MedicalStockMovement.MovementType.QUARANTINE,
                    reason="Cold-chain excursion", reference=str(observation.pk), responsible_user=actor,
                )
        return observation


def refresh_medical_alerts(program=None):
    today = timezone.localdate()
    batches = MedicalBatch.objects.select_related("program", "product", "warehouse").filter(available_quantity__gt=0)
    if program:
        batches = batches.filter(program=program)
    created = 0
    for batch in batches:
        alerts = []
        if batch.expiry_date and batch.expiry_date <= today:
            alerts.append((MedicalAlert.AlertType.EXPIRED, f"Batch {batch.lot_number} has expired."))
            if batch.status == MedicalBatch.Status.AVAILABLE:
                batch.status = MedicalBatch.Status.EXPIRED
                batch.save(update_fields=["status", "updated_at"])
        elif batch.expiry_date and batch.expiry_date <= today + timedelta(days=30):
            alerts.append((MedicalAlert.AlertType.EXPIRING, f"Batch {batch.lot_number} expires within 30 days."))
        if batch.status == MedicalBatch.Status.QUARANTINED:
            alerts.append((MedicalAlert.AlertType.QUARANTINED, f"Batch {batch.lot_number} is quarantined."))
        if batch.status == MedicalBatch.Status.DAMAGED:
            alerts.append((MedicalAlert.AlertType.DAMAGED, f"Batch {batch.lot_number} is marked damaged."))
        for alert_type, message in alerts:
            _, was_created = MedicalAlert.objects.get_or_create(
                program=batch.program, product=batch.product, batch=batch, warehouse=batch.warehouse,
                alert_type=alert_type, status=MedicalAlert.Status.OPEN, defaults={"message": message},
            )
            created += int(was_created)
    products = {batch.product for batch in batches}
    for product in products:
        total = MedicalBatch.objects.filter(product=product, status=MedicalBatch.Status.AVAILABLE).aggregate(total=Sum("available_quantity"))["total"] or 0
        if total <= product.minimum_stock_threshold:
            _, was_created = MedicalAlert.objects.get_or_create(
                program=product.program, product=product, batch=None, warehouse=None,
                alert_type=MedicalAlert.AlertType.LOW_STOCK, status=MedicalAlert.Status.OPEN,
                defaults={"message": f"Available stock ({total}) is at or below the threshold ({product.minimum_stock_threshold})."},
            )
            created += int(was_created)
    return created
