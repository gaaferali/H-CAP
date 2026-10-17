import json
from hashlib import sha256

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from accounts.models import IdempotencyRecord, User
from accounts.viewsets import RoleProtectedTenantViewSet
from operations.models import Warehouse
from reporting.audit import audit, json_safe
from programs.models import Program
from programs.services import require_program_active, require_reason

from .models import (
    ControlledProductAuthorization,
    EquipmentMovement,
    MedicalAlert,
    MedicalBatch,
    MedicalDistributionAllocation,
    MedicalDistributionIssue,
    MedicalEntitlement,
    MedicalEquipment,
    MedicalProduct,
    MedicalStockMovement,
    TemperatureObservation,
)
from .serializers import (
    ControlledProductAuthorizationSerializer,
    EquipmentMovementSerializer,
    MedicalAlertSerializer,
    MedicalBatchSerializer,
    MedicalDistributionAllocationSerializer,
    MedicalDistributionIssueSerializer,
    MedicalEntitlementSerializer,
    MedicalEquipmentSerializer,
    MedicalProductSerializer,
    MedicalStockMovementSerializer,
    TemperatureObservationSerializer,
)
from .services import (
    adjust_batch,
    allocate_entitlement,
    cancel_entitlement,
    cancel_distribution_allocation,
    create_distribution_allocation,
    issue_distribution,
    receive_batch,
    record_temperature,
    refresh_medical_alerts,
    transfer_batch,
)


MEDICAL_ROLES = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER, User.Role.FINANCE, User.Role.AUDITOR}
MEDICAL_WRITE_ROLES = {User.Role.ADMIN, User.Role.MANAGER}


# Prevents repeated medical action buttons from applying the same change twice.
def _idempotent_action(request, callback):
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        return callback()
    fingerprint = sha256(json.dumps(request.data, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    with transaction.atomic():
        record, created = IdempotencyRecord.objects.get_or_create(
            tenant=request.user.tenant,
            actor=request.user,
            endpoint=request.path,
            idempotency_key=key,
            defaults={"request_fingerprint": fingerprint, "response_body": {}},
        )
        record = IdempotencyRecord.objects.select_for_update().get(pk=record.pk)
        if not created:
            if record.request_fingerprint != fingerprint:
                raise ValidationError({"idempotency_key": "This idempotency key was already used with different data."})
            return Response(record.response_body, status=record.response_status)
        response = callback()
        record.response_status = response.status_code
        record.response_body = json_safe(response.data)
        record.save(update_fields=["response_status", "response_body"])
        return response


class MedicalBaseViewSet(RoleProtectedTenantViewSet):
    allowed_roles = MEDICAL_ROLES
    write_roles = MEDICAL_WRITE_ROLES
    tenant_field = "program__tenant"


# Lists and creates medical products for enabled programs.
class MedicalProductViewSet(MedicalBaseViewSet):
    queryset = MedicalProduct.objects.select_related("program", "created_by").all()
    serializer_class = MedicalProductSerializer


# Receives batches and exposes controlled stock operations.
class MedicalBatchViewSet(MedicalBaseViewSet):
    queryset = MedicalBatch.objects.select_related("program", "product", "warehouse", "created_by").all()
    serializer_class = MedicalBatchSerializer

    def perform_create(self, serializer):
        batch = receive_batch(serializer, self.request.user)
        audit(self.request.user, "MEDICAL_BATCH_RECEIVED", batch, after=self.get_serializer(batch).data, tenant=batch.program.tenant)

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        def operation():
            try:
                quantity = int(request.data.get("quantity", 0))
            except (TypeError, ValueError):
                raise ValidationError({"quantity": "Enter a positive whole number."})
            batch = adjust_batch(pk, quantity, request.data.get("direction", ""), request.data.get("reason", ""), request.user)
            audit(request.user, "MEDICAL_STOCK_ADJUSTED", batch, after={"direction": request.data.get("direction"), "quantity": quantity}, tenant=batch.program.tenant)
            return Response(self.get_serializer(batch).data)
        return _idempotent_action(request, operation)

    @action(detail=True, methods=["post"])
    def quarantine(self, request, pk=None):
        def operation():
            with transaction.atomic():
                batch = MedicalBatch.objects.select_for_update().get(pk=pk)
                if not request.user.is_superuser and batch.program.tenant_id != request.user.tenant_id:
                    raise PermissionDenied("This batch belongs to another tenant.")
                if batch.status in {MedicalBatch.Status.EXPIRED, MedicalBatch.Status.DISPOSED}:
                    raise ValidationError({"status": "Expired or disposed stock cannot be quarantined."})
                batch.status = MedicalBatch.Status.QUARANTINED
                batch.save(update_fields=["status", "updated_at"])
                MedicalStockMovement.objects.create(
                    program=batch.program, product=batch.product, batch=batch, source_warehouse=batch.warehouse,
                    quantity=max(batch.available_quantity, 1), movement_type=MedicalStockMovement.MovementType.QUARANTINE,
                    reason=request.data.get("reason", "Manual quarantine"), reference=str(batch.pk), responsible_user=request.user,
                )
                refresh_medical_alerts(batch.program)
                audit(request.user, "MEDICAL_BATCH_QUARANTINED", batch, after=self.get_serializer(batch).data, tenant=batch.program.tenant)
                return Response(self.get_serializer(batch).data)
        return _idempotent_action(request, operation)

    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        def operation():
            try:
                quantity = int(request.data.get("quantity", 0))
                destination = Warehouse.objects.get(pk=request.data.get("destination_warehouse"))
            except (TypeError, ValueError, Warehouse.DoesNotExist):
                raise ValidationError("Choose a destination warehouse and enter a positive whole quantity.")
            source, received = transfer_batch(pk, destination, quantity, request.data.get("reason", ""), request.user)
            audit(request.user, "MEDICAL_STOCK_TRANSFERRED", source, after={"destination_batch": str(received.pk), "quantity": quantity}, tenant=source.program.tenant)
            return Response({"source": self.get_serializer(source).data, "destination": self.get_serializer(received).data})
        return _idempotent_action(request, operation)

    @action(detail=True, methods=["post"])
    def dispose(self, request, pk=None):
        def operation():
            with transaction.atomic():
                batch = MedicalBatch.objects.select_for_update().get(pk=pk)
                if not request.user.is_superuser and batch.program.tenant_id != request.user.tenant_id:
                    raise PermissionDenied("This batch belongs to another tenant.")
                quantity = batch.available_quantity
                if quantity < 1:
                    raise ValidationError({"quantity": "This batch has no available stock to dispose."})
                batch.available_quantity = 0
                batch.status = MedicalBatch.Status.DISPOSED
                batch.save(update_fields=["available_quantity", "status", "updated_at"])
                MedicalStockMovement.objects.create(
                    program=batch.program, product=batch.product, batch=batch, source_warehouse=batch.warehouse,
                    quantity=quantity, movement_type=MedicalStockMovement.MovementType.DISPOSAL,
                    reason=request.data.get("reason", "Disposed stock"), reference=str(batch.pk), responsible_user=request.user,
                )
                audit(request.user, "MEDICAL_BATCH_DISPOSED", batch, after={"quantity": quantity}, tenant=batch.program.tenant)
                return Response(self.get_serializer(batch).data)
        return _idempotent_action(request, operation)


class MedicalStockMovementViewSet(MedicalBaseViewSet):
    queryset = MedicalStockMovement.objects.select_related("program", "product", "batch", "source_warehouse", "destination_warehouse", "responsible_user").all()
    serializer_class = MedicalStockMovementSerializer
    http_method_names = ["get", "head", "options"]


# Creates, approves, allocates, and cancels medical entitlements.
class MedicalEntitlementViewSet(MedicalBaseViewSet):
    queryset = MedicalEntitlement.objects.select_related("program", "household", "beneficiary", "product", "warehouse", "batch").all()
    serializer_class = MedicalEntitlementSerializer
    write_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER}

    @transaction.atomic
    def perform_create(self, serializer):
        program = Program.objects.select_for_update().get(pk=serializer.validated_data["program"].pk)
        require_program_active(program, "medical entitlement creation")
        entitlement = serializer.save(program=program, created_by=self.request.user)
        audit(self.request.user, "MEDICALENTITLEMENT_CREATED", entitlement, after=self.get_serializer(entitlement).data, tenant=program.tenant)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        def operation():
            with transaction.atomic():
                entitlement = MedicalEntitlement.objects.select_for_update().get(pk=pk)
                if not request.user.is_superuser and entitlement.program.tenant_id != request.user.tenant_id:
                    raise PermissionDenied("This entitlement belongs to another tenant.")
                if entitlement.status != MedicalEntitlement.Status.PENDING:
                    raise ValidationError({"status": "Only pending entitlements can be approved."})
                entitlement.status = MedicalEntitlement.Status.APPROVED
                entitlement.approved_by = request.user
                entitlement.approved_at = timezone.now()
                entitlement.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
                audit(request.user, "MEDICAL_ENTITLEMENT_APPROVED", entitlement, after=self.get_serializer(entitlement).data, tenant=entitlement.program.tenant)
                return Response(self.get_serializer(entitlement).data)
        return _idempotent_action(request, operation)

    @action(detail=True, methods=["post"])
    def allocate(self, request, pk=None):
        def operation():
            entitlement = allocate_entitlement(pk, request.user, request.data.get("batch"))
            audit(request.user, "MEDICAL_ENTITLEMENT_ALLOCATED", entitlement, after=self.get_serializer(entitlement).data, tenant=entitlement.program.tenant)
            return Response(self.get_serializer(entitlement).data)
        return _idempotent_action(request, operation)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        def operation():
            reason = require_reason(request.data.get("reason"))
            entitlement = cancel_entitlement(pk, request.user, reason)
            audit(request.user, "MEDICAL_ENTITLEMENT_CANCELLED", entitlement, after={**self.get_serializer(entitlement).data, "reason": reason}, tenant=entitlement.program.tenant)
            return Response(self.get_serializer(entitlement).data)
        return _idempotent_action(request, operation)


class MedicalDistributionAllocationViewSet(MedicalBaseViewSet):
    queryset = MedicalDistributionAllocation.objects.select_related(
        "entitlement__program", "entitlement__household", "entitlement__beneficiary", "entitlement__product",
        "entitlement__warehouse", "event", "batch"
    ).all()
    serializer_class = MedicalDistributionAllocationSerializer
    tenant_field = "entitlement__program__tenant"

    def perform_create(self, serializer):
        allocation = create_distribution_allocation(serializer, self.request.user)
        audit(self.request.user, "MEDICAL_DISTRIBUTION_ALLOCATED", allocation, after=self.get_serializer(allocation).data, tenant=allocation.entitlement.program.tenant)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        def operation():
            reason = require_reason(request.data.get("reason"))
            allocation = cancel_distribution_allocation(pk, request.user, reason)
            audit(request.user, "MEDICAL_DISTRIBUTION_ALLOCATION_CANCELLED", allocation, after={**self.get_serializer(allocation).data, "reason": reason}, tenant=allocation.entitlement.program.tenant)
            return Response(self.get_serializer(allocation).data)
        return _idempotent_action(request, operation)


# Records the final medical delivery made to a household or beneficiary.
class MedicalDistributionIssueViewSet(MedicalBaseViewSet):
    queryset = MedicalDistributionIssue.objects.select_related(
        "allocation__entitlement__program", "allocation__entitlement__household",
        "allocation__entitlement__beneficiary", "allocation__entitlement__product", "allocation__batch"
    ).all()
    serializer_class = MedicalDistributionIssueSerializer
    tenant_field = "allocation__entitlement__program__tenant"

    def perform_create(self, serializer):
        issue = issue_distribution(serializer, self.request.user)
        audit(self.request.user, "MEDICAL_DISTRIBUTION_ISSUED", issue, after=self.get_serializer(issue).data, tenant=issue.allocation.entitlement.program.tenant)


class MedicalAlertViewSet(MedicalBaseViewSet):
    queryset = MedicalAlert.objects.select_related("program", "product", "batch", "warehouse", "acknowledged_by").all()
    serializer_class = MedicalAlertSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def partial_update(self, request, *args, **kwargs):
        alert = self.get_object()
        requested = request.data.get("status")
        if requested not in {MedicalAlert.Status.ACKNOWLEDGED, MedicalAlert.Status.RESOLVED}:
            raise ValidationError({"status": "Choose ACKNOWLEDGED or RESOLVED."})
        alert.status = requested
        if requested == MedicalAlert.Status.ACKNOWLEDGED:
            alert.acknowledged_by = request.user
            alert.acknowledged_at = timezone.now()
        else:
            alert.resolved_at = timezone.now()
        alert.save()
        audit(request.user, "MEDICAL_ALERT_UPDATED", alert, after=self.get_serializer(alert).data, tenant=alert.program.tenant)
        return Response(self.get_serializer(alert).data)


class TemperatureObservationViewSet(MedicalBaseViewSet):
    queryset = TemperatureObservation.objects.select_related("batch__program", "batch__product", "batch__warehouse", "responsible_user").all()
    serializer_class = TemperatureObservationSerializer
    tenant_field = "batch__program__tenant"

    def perform_create(self, serializer):
        observation = record_temperature(serializer, self.request.user)
        audit(self.request.user, "MEDICAL_TEMPERATURE_RECORDED", observation, after=self.get_serializer(observation).data, tenant=observation.batch.program.tenant)


class ControlledProductAuthorizationViewSet(MedicalBaseViewSet):
    queryset = ControlledProductAuthorization.objects.select_related("program", "user", "granted_by").all()
    serializer_class = ControlledProductAuthorizationSerializer
    write_roles = {User.Role.ADMIN}

    def perform_create(self, serializer):
        instance = serializer.save(granted_by=self.request.user)
        audit(self.request.user, "CONTROLLED_PRODUCT_AUTHORIZATION_GRANTED", instance, after=self.get_serializer(instance).data, tenant=instance.program.tenant)


# Returns the medical equipment register for the current tenant.
class MedicalEquipmentViewSet(MedicalBaseViewSet):
    queryset = MedicalEquipment.objects.select_related("program", "warehouse", "product", "responsible_person").all()
    serializer_class = MedicalEquipmentSerializer


class EquipmentMovementViewSet(MedicalBaseViewSet):
    queryset = EquipmentMovement.objects.select_related("equipment__program", "source_warehouse", "destination_warehouse", "assigned_to", "responsible_user").all()
    serializer_class = EquipmentMovementSerializer
    tenant_field = "equipment__program__tenant"

    def perform_create(self, serializer):
        data = serializer.validated_data
        equipment = data["equipment"]
        source = data.get("source_warehouse")
        destination = data.get("destination_warehouse")
        if source and source.program_id != equipment.program_id or destination and destination.program_id != equipment.program_id:
            raise ValidationError("Equipment and warehouses must belong to the same program.")
        movement = serializer.save(responsible_user=self.request.user)
        if destination:
            equipment.warehouse = destination
        if movement.movement_type == EquipmentMovement.MovementType.ASSIGN:
            equipment.availability = MedicalEquipment.Availability.ASSIGNED
            equipment.responsible_person = movement.assigned_to
        elif movement.movement_type == EquipmentMovement.MovementType.RETURN:
            equipment.availability = MedicalEquipment.Availability.AVAILABLE
            equipment.responsible_person = None
        elif movement.movement_type == EquipmentMovement.MovementType.MAINTENANCE:
            equipment.availability = MedicalEquipment.Availability.MAINTENANCE
        elif movement.movement_type == EquipmentMovement.MovementType.RETIRE:
            equipment.availability = MedicalEquipment.Availability.RETIRED
            equipment.condition = MedicalEquipment.Condition.RETIRED
            equipment.active = False
        equipment.condition = data["condition"]
        equipment.save()
        audit(self.request.user, "MEDICAL_EQUIPMENT_MOVED", movement, after=self.get_serializer(movement).data, tenant=equipment.program.tenant)


# Returns the medical dashboard totals and refreshes operational alerts.
@api_view(["GET", "POST"])
def medical_dashboard_view(request):
    if request.user.role not in MEDICAL_ROLES:
        raise PermissionDenied("Your role does not have permission for Medical Assistance.")
    programs = request.user.tenant.programs.filter(medical_enabled=True)
    program_id = request.query_params.get("program")
    if program_id:
        programs = programs.filter(pk=program_id)
    if request.method == "POST":
        if request.user.role not in MEDICAL_WRITE_ROLES:
            raise PermissionDenied("Your role cannot refresh medical alerts.")
        created = sum(refresh_medical_alerts(program) for program in programs)
    else:
        created = 0
    batches = MedicalBatch.objects.filter(program__in=programs)
    return Response({
        "program_count": programs.count(),
        "product_count": MedicalProduct.objects.filter(program__in=programs, active=True).count(),
        "batch_count": batches.count(),
        "available_quantity": batches.filter(status=MedicalBatch.Status.AVAILABLE).aggregate(total=Sum("available_quantity"))["total"] or 0,
        "pending_entitlements": MedicalEntitlement.objects.filter(program__in=programs, status=MedicalEntitlement.Status.PENDING).count(),
        "open_alerts": MedicalAlert.objects.filter(program__in=programs, status=MedicalAlert.Status.OPEN).count(),
        "equipment_by_availability": list(MedicalEquipment.objects.filter(program__in=programs).values("availability").annotate(count=Count("id")).order_by("availability")),
        "alerts_created": created,
    })
