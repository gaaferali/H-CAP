import uuid

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from accounts.models import Tenant, User
from assistance.models import Beneficiary, Household
from operations.models import DistributionEvent, Warehouse
from programs.models import Program


# Medical products table with storage, control, and cold-chain requirements.
class MedicalProduct(models.Model):
    class Category(models.TextChoices):
        MEDICINE = "MEDICINE", "Medicine"
        THERAPEUTIC_NUTRITION = "THERAPEUTIC_NUTRITION", "Therapeutic nutrition"
        CONSUMABLE = "CONSUMABLE", "Medical consumable or supply"
        EQUIPMENT = "EQUIPMENT", "Medical equipment"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="medical_products")
    sku = models.CharField(max_length=120)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=40, choices=Category.choices)
    description = models.TextField(blank=True)
    unit_of_measurement = models.CharField(max_length=80)
    storage_requirements = models.TextField(blank=True)
    refrigeration_required = models.BooleanField(default=False)
    minimum_temperature = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    maximum_temperature = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    controlled = models.BooleanField(default=False)
    minimum_stock_threshold = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_medical_products")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "medical_product"
        ordering = ["name", "sku"]
        constraints = [
            models.UniqueConstraint(fields=["program", "sku"], name="unique_medical_sku_per_program"),
            models.CheckConstraint(
                condition=(
                    models.Q(minimum_temperature__isnull=True)
                    | models.Q(maximum_temperature__isnull=True)
                    | models.Q(minimum_temperature__lte=models.F("maximum_temperature"))
                ),
                name="medical_product_temperature_range",
            ),
        ]


# Medical batches table for lot numbers, expiry dates, and available quantity.
class MedicalBatch(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        QUARANTINED = "QUARANTINED", "Quarantined"
        DAMAGED = "DAMAGED", "Damaged"
        EXPIRED = "EXPIRED", "Expired"
        DISPOSED = "DISPOSED", "Disposed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="medical_batches")
    product = models.ForeignKey(MedicalProduct, on_delete=models.PROTECT, related_name="batches")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="medical_batches")
    lot_number = models.CharField(max_length=120)
    manufacturing_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    received_quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    available_quantity = models.PositiveIntegerField(default=0)
    supplier_source = models.CharField(max_length=255)
    received_date = models.DateField(default=timezone.localdate)
    storage_location = models.CharField(max_length=255, blank=True)
    intended_distribution_location = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_medical_batches")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "medical_batch"
        ordering = ["expiry_date", "product__name", "lot_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "warehouse", "lot_number"],
                name="unique_medical_lot_per_warehouse",
            ),
            models.CheckConstraint(
                condition=models.Q(received_quantity__gte=1),
                name="medical_batch_received_positive",
            ),
        ]


# Append-only medical stock history for every quantity change.
class MedicalStockMovement(models.Model):
    class MovementType(models.TextChoices):
        RECEIPT = "RECEIPT", "Receipt"
        ALLOCATION = "ALLOCATION", "Allocation"
        ADJUSTMENT_IN = "ADJUSTMENT_IN", "Positive adjustment"
        ADJUSTMENT_OUT = "ADJUSTMENT_OUT", "Negative adjustment"
        TRANSFER_OUT = "TRANSFER_OUT", "Transfer out"
        TRANSFER_IN = "TRANSFER_IN", "Transfer in"
        DISTRIBUTION = "DISTRIBUTION", "Distribution"
        RETURN = "RETURN", "Return"
        DAMAGED = "DAMAGED", "Damaged"
        EXPIRED = "EXPIRED", "Expired"
        QUARANTINE = "QUARANTINE", "Quarantine"
        DISPOSAL = "DISPOSAL", "Disposal"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="medical_stock_movements")
    product = models.ForeignKey(MedicalProduct, on_delete=models.PROTECT, related_name="stock_movements")
    batch = models.ForeignKey(MedicalBatch, on_delete=models.PROTECT, related_name="movements")
    source_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="medical_stock_out_movements",
    )
    destination_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="medical_stock_in_movements",
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    movement_type = models.CharField(max_length=30, choices=MovementType.choices)
    reason = models.TextField(blank=True)
    reference = models.CharField(max_length=255, blank=True)
    responsible_user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="medical_stock_movements")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "medical_stock_movement"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name="medical_stock_movement_positive")
        ]


# Medical entitlements table for approved household or beneficiary supplies.
class MedicalEntitlement(models.Model):
    class RecipientType(models.TextChoices):
        HOUSEHOLD = "HOUSEHOLD", "Household"
        INDIVIDUAL = "INDIVIDUAL", "Individual beneficiary"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        ALLOCATED = "ALLOCATED", "Allocated"
        DISTRIBUTED = "DISTRIBUTED", "Distributed"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="medical_entitlements")
    recipient_type = models.CharField(max_length=20, choices=RecipientType.choices)
    household = models.ForeignKey(Household, on_delete=models.PROTECT, related_name="medical_entitlements")
    beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="medical_entitlements",
    )
    product = models.ForeignKey(MedicalProduct, on_delete=models.PROTECT, related_name="entitlements")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="medical_entitlements")
    batch = models.ForeignKey(
        MedicalBatch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="entitlements",
    )
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    approved_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_medical_entitlements",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_medical_entitlements")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "medical_entitlement"
        ordering = ["-created_at"]


# Assigns a medical entitlement and batch to a distribution event.
class MedicalDistributionAllocation(models.Model):
    class Status(models.TextChoices):
        ALLOCATED = "ALLOCATED", "Allocated"
        DISTRIBUTED = "DISTRIBUTED", "Distributed"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entitlement = models.ForeignKey(MedicalEntitlement, on_delete=models.PROTECT, related_name="distribution_allocations")
    event = models.ForeignKey(DistributionEvent, on_delete=models.PROTECT, related_name="medical_allocations")
    batch = models.ForeignKey(MedicalBatch, on_delete=models.PROTECT, related_name="distribution_allocations")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ALLOCATED)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_medical_allocations")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "medical_distribution_allocation"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["entitlement", "event", "batch"],
                name="unique_medical_allocation_event_batch",
            )
        ]


# Stores the actual medical quantity delivered to the recipient.
class MedicalDistributionIssue(models.Model):
    class DeliveryStatus(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED", "Partially received"
        NOT_RECEIVED = "NOT_RECEIVED", "Not received"
        NO_SHOW = "NO_SHOW", "No show"
        REJECTED = "REJECTED", "Rejected"
        DAMAGED = "DAMAGED", "Damaged"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    allocation = models.ForeignKey(MedicalDistributionAllocation, on_delete=models.PROTECT, related_name="issues")
    actual_quantity = models.PositiveIntegerField(default=0)
    delivery_status = models.CharField(max_length=30, choices=DeliveryStatus.choices)
    distribution_date = models.DateField(default=timezone.localdate)
    evidence = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_medical_issues")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "medical_distribution_issue"
        ordering = ["-created_at"]


# Medical alerts table for low stock, expiry, and temperature risks.
class MedicalAlert(models.Model):
    class AlertType(models.TextChoices):
        EXPIRING = "EXPIRING", "Expiring within 30 days"
        EXPIRED = "EXPIRED", "Expired"
        LOW_STOCK = "LOW_STOCK", "Low stock"
        QUARANTINED = "QUARANTINED", "Quarantined stock"
        DAMAGED = "DAMAGED", "Damaged stock"
        COLD_CHAIN = "COLD_CHAIN", "Cold-chain excursion"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
        RESOLVED = "RESOLVED", "Resolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="medical_alerts")
    product = models.ForeignKey(MedicalProduct, on_delete=models.PROTECT, related_name="alerts")
    batch = models.ForeignKey(MedicalBatch, on_delete=models.PROTECT, null=True, blank=True, related_name="alerts")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, null=True, blank=True, related_name="medical_alerts")
    alert_type = models.CharField(max_length=30, choices=AlertType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    message = models.TextField()
    detected_at = models.DateTimeField(default=timezone.now)
    acknowledged_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="acknowledged_medical_alerts",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "medical_alert"
        ordering = ["status", "-detected_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["program", "product", "batch", "warehouse", "alert_type"],
                condition=models.Q(status="OPEN"),
                nulls_distinct=False,
                name="unique_open_medical_alert",
            )
        ]


class TemperatureObservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(MedicalBatch, on_delete=models.PROTECT, related_name="temperature_observations")
    temperature = models.DecimalField(max_digits=6, decimal_places=2)
    observed_at = models.DateTimeField(default=timezone.now)
    excursion = models.BooleanField(default=False)
    quarantine_decision = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    responsible_user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="temperature_observations")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "medical_temperature_observation"
        ordering = ["-observed_at"]


class ControlledProductAuthorization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="controlled_product_authorizations")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="controlled_product_authorizations")
    can_receive = models.BooleanField(default=False)
    can_issue = models.BooleanField(default=False)
    permit_reference = models.CharField(max_length=255)
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    granted_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="granted_controlled_product_authorizations")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "medical_controlled_authorization"
        ordering = ["user__full_name"]
        constraints = [
            models.UniqueConstraint(fields=["program", "user"], name="unique_controlled_authorization_per_program_user"),
            models.CheckConstraint(
                condition=models.Q(valid_until__isnull=True) | models.Q(valid_until__gte=models.F("valid_from")),
                name="controlled_authorization_date_range",
            ),
        ]


# Medical equipment table for tracked assets, condition, and availability.
class MedicalEquipment(models.Model):
    class Condition(models.TextChoices):
        GOOD = "GOOD", "Good"
        NEEDS_MAINTENANCE = "NEEDS_MAINTENANCE", "Needs maintenance"
        DAMAGED = "DAMAGED", "Damaged"
        RETIRED = "RETIRED", "Retired"

    class Availability(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        ASSIGNED = "ASSIGNED", "Assigned"
        MAINTENANCE = "MAINTENANCE", "In maintenance"
        RETIRED = "RETIRED", "Retired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="medical_equipment")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="medical_equipment")
    product = models.ForeignKey(MedicalProduct, on_delete=models.PROTECT, null=True, blank=True, related_name="equipment_assets")
    name = models.CharField(max_length=255)
    asset_identifier = models.CharField(max_length=120)
    serial_number = models.CharField(max_length=160, blank=True)
    category = models.CharField(max_length=120)
    condition = models.CharField(max_length=30, choices=Condition.choices, default=Condition.GOOD)
    availability = models.CharField(max_length=30, choices=Availability.choices, default=Availability.AVAILABLE)
    responsible_person = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="responsible_medical_equipment",
    )
    last_check_date = models.DateField(null=True, blank=True)
    next_maintenance_date = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_medical_equipment")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "medical_equipment"
        ordering = ["name", "asset_identifier"]
        constraints = [
            models.UniqueConstraint(fields=["program", "asset_identifier"], name="unique_medical_asset_per_program")
        ]


# Records equipment assignment, transfer, return, maintenance, and retirement.
class EquipmentMovement(models.Model):
    class MovementType(models.TextChoices):
        RECEIVE = "RECEIVE", "Receive"
        ASSIGN = "ASSIGN", "Assign"
        RETURN = "RETURN", "Return"
        TRANSFER = "TRANSFER", "Transfer"
        MAINTENANCE = "MAINTENANCE", "Maintenance"
        RETIRE = "RETIRE", "Retire"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment = models.ForeignKey(MedicalEquipment, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    source_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="equipment_movements_out",
    )
    destination_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="equipment_movements_in",
    )
    assigned_to = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="equipment_assignments")
    condition = models.CharField(max_length=30, choices=MedicalEquipment.Condition.choices)
    notes = models.TextField(blank=True)
    responsible_user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="equipment_movements")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "medical_equipment_movement"
        ordering = ["-created_at"]
