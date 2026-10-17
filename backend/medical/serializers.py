from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from assistance.models import HouseholdEligibility
from assistance.serializer_helpers import approved_household_modality, household_reference

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


def validate_tenant(serializer, program):
    request = serializer.context.get("request")
    if request and not request.user.is_superuser and program.tenant_id != request.user.tenant_id:
        raise serializers.ValidationError("Program belongs to another tenant.")


# Validates medical products and their storage requirements.
class MedicalProductSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    stock_available = serializers.SerializerMethodField()

    class Meta:
        model = MedicalProduct
        fields = "__all__"
        read_only_fields = ["created_by", "created_at", "updated_at"]

    def get_stock_available(self, obj):
        return obj.batches.filter(status=MedicalBatch.Status.AVAILABLE).aggregate(total=Sum("available_quantity"))["total"] or 0

    def validate(self, attrs):
        program = attrs.get("program", getattr(self.instance, "program", None))
        if program and not program.medical_enabled:
            raise serializers.ValidationError({"program": "Medical assistance is disabled for this program."})
        if program:
            validate_tenant(self, program)
        minimum = attrs.get("minimum_temperature", getattr(self.instance, "minimum_temperature", None))
        maximum = attrs.get("maximum_temperature", getattr(self.instance, "maximum_temperature", None))
        refrigerated = attrs.get("refrigeration_required", getattr(self.instance, "refrigeration_required", False))
        if refrigerated and (minimum is None or maximum is None):
            raise serializers.ValidationError("Minimum and maximum temperatures are required for refrigerated products.")
        return attrs


# Validates batch quantities and returns readable product and warehouse details.
class MedicalBatchSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = MedicalBatch
        fields = "__all__"
        read_only_fields = ["available_quantity", "created_by", "created_at", "updated_at"]

    def get_is_expired(self, obj):
        from django.utils import timezone
        return bool(obj.expiry_date and obj.expiry_date <= timezone.localdate())

    def validate(self, attrs):
        program = attrs.get("program", self.instance.program if self.instance else None)
        product = attrs.get("product", self.instance.product if self.instance else None)
        warehouse = attrs.get("warehouse", self.instance.warehouse if self.instance else None)
        validate_tenant(self, program)
        if not program.medical_enabled:
            raise serializers.ValidationError({"program": "Medical assistance is disabled for this program."})
        if product.program_id != program.id or warehouse.program_id != program.id:
            raise serializers.ValidationError("Product, warehouse, and program must match.")
        if not product.active or not warehouse.active:
            raise serializers.ValidationError("Product and warehouse must be active.")
        manufacturing = attrs.get("manufacturing_date")
        expiry = attrs.get("expiry_date")
        if manufacturing and expiry and expiry <= manufacturing:
            raise serializers.ValidationError({"expiry_date": "Expiry date must be after manufacturing date."})
        if self.instance is None and expiry and expiry <= timezone.localdate():
            raise serializers.ValidationError({"expiry_date": "Expired stock cannot be received as available inventory."})
        return attrs


class MedicalStockMovementSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    batch_lot_number = serializers.CharField(source="batch.lot_number", read_only=True)
    source_warehouse_name = serializers.CharField(source="source_warehouse.name", read_only=True)
    destination_warehouse_name = serializers.CharField(source="destination_warehouse.name", read_only=True)
    responsible_user_name = serializers.CharField(source="responsible_user.full_name", read_only=True)

    class Meta:
        model = MedicalStockMovement
        fields = "__all__"
        read_only_fields = [field.name for field in MedicalStockMovement._meta.fields]


# Checks approved medical eligibility before creating a recipient entitlement.
class MedicalEntitlementSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    household_reference = serializers.SerializerMethodField()
    beneficiary_name = serializers.CharField(source="beneficiary.full_name", read_only=True)
    beneficiary_number = serializers.CharField(source="beneficiary.number", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    batch_lot_number = serializers.CharField(source="batch.lot_number", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = MedicalEntitlement
        fields = "__all__"
        read_only_fields = ["batch", "approved_by", "approved_at", "created_by", "created_at", "updated_at"]

    def get_household_reference(self, obj):
        return household_reference(obj.household)

    def validate(self, attrs):
        instance = self.instance
        program = attrs.get("program", getattr(instance, "program", None))
        household = attrs.get("household", getattr(instance, "household", None))
        beneficiary = attrs.get("beneficiary", getattr(instance, "beneficiary", None))
        product = attrs.get("product", getattr(instance, "product", None))
        warehouse = attrs.get("warehouse", getattr(instance, "warehouse", None))
        recipient_type = attrs.get("recipient_type", getattr(instance, "recipient_type", None))
        validate_tenant(self, program)
        if not program.medical_enabled:
            raise serializers.ValidationError({"program": "Medical assistance is disabled for this program."})
        if household.program_id != program.id or household.tenant_id != program.tenant_id:
            raise serializers.ValidationError({"household": "Household must belong to the selected program."})
        if product.program_id != program.id or warehouse.program_id != program.id or not product.active or not warehouse.active:
            raise serializers.ValidationError("Product and warehouse must be active and belong to the selected program.")
        if recipient_type == MedicalEntitlement.RecipientType.INDIVIDUAL:
            if not beneficiary or beneficiary.household_id != household.id:
                raise serializers.ValidationError({"beneficiary": "Select a beneficiary in the entitlement household."})
        elif beneficiary:
            raise serializers.ValidationError({"beneficiary": "Household entitlements must not select an individual beneficiary."})
        eligibility = household.eligibility_decisions.filter(program=program)
        if eligibility.exists() and not eligibility.filter(status=HouseholdEligibility.Status.ELIGIBLE).exists():
            raise serializers.ValidationError({"household": "Household is not eligible for this program."})
        if not approved_household_modality(household, program, ["MEDICAL"]):
            raise serializers.ValidationError({"household": "Household must have approved Medical Assistance enrollment."})
        if instance and instance.status in {MedicalEntitlement.Status.ALLOCATED, MedicalEntitlement.Status.DISTRIBUTED}:
            immutable = {"program", "household", "beneficiary", "recipient_type", "product", "warehouse", "quantity"}
            if immutable.intersection(attrs):
                raise serializers.ValidationError("Allocated or distributed entitlement details cannot be changed.")
        return attrs


# Validates the event, entitlement, and batch used for medical allocation.
class MedicalDistributionAllocationSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="entitlement.program.name", read_only=True)
    household_reference = serializers.SerializerMethodField()
    beneficiary_name = serializers.CharField(source="entitlement.beneficiary.full_name", read_only=True)
    product_name = serializers.CharField(source="entitlement.product.name", read_only=True)
    warehouse_name = serializers.CharField(source="entitlement.warehouse.name", read_only=True)
    batch_lot_number = serializers.CharField(source="batch.lot_number", read_only=True)
    event_location = serializers.CharField(source="event.location", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = MedicalDistributionAllocation
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]

    def get_household_reference(self, obj):
        return household_reference(obj.entitlement.household)

    def validate(self, attrs):
        entitlement = attrs.get("entitlement", self.instance.entitlement if self.instance else None)
        event = attrs.get("event", self.instance.event if self.instance else None)
        batch = attrs.get("batch", self.instance.batch if self.instance else None)
        validate_tenant(self, entitlement.program)
        if event.program_id != entitlement.program_id or batch.program_id != entitlement.program_id:
            raise serializers.ValidationError("Entitlement, event, and batch must belong to the same program.")
        return attrs


# Validates the delivered medical quantity and recipient evidence.
class MedicalDistributionIssueSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="allocation.entitlement.program.name", read_only=True)
    household_reference = serializers.SerializerMethodField()
    beneficiary_name = serializers.CharField(source="allocation.entitlement.beneficiary.full_name", read_only=True)
    product_name = serializers.CharField(source="allocation.entitlement.product.name", read_only=True)
    batch_lot_number = serializers.CharField(source="allocation.batch.lot_number", read_only=True)
    delivery_status_display = serializers.CharField(source="get_delivery_status_display", read_only=True)

    class Meta:
        model = MedicalDistributionIssue
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]

    def get_household_reference(self, obj):
        return household_reference(obj.allocation.entitlement.household)

    def validate(self, attrs):
        allocation = attrs.get("allocation", self.instance.allocation if self.instance else None)
        validate_tenant(self, allocation.entitlement.program)
        actual = attrs.get("actual_quantity", self.instance.actual_quantity if self.instance else 0)
        if actual > allocation.quantity:
            raise serializers.ValidationError({"actual_quantity": "Issued quantity cannot exceed the allocation."})
        return attrs


class MedicalAlertSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    batch_lot_number = serializers.CharField(source="batch.lot_number", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    alert_type_display = serializers.CharField(source="get_alert_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = MedicalAlert
        fields = "__all__"
        read_only_fields = ["program", "product", "batch", "warehouse", "alert_type", "message", "detected_at", "acknowledged_by", "acknowledged_at", "resolved_at"]


class TemperatureObservationSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="batch.product.name", read_only=True)
    batch_lot_number = serializers.CharField(source="batch.lot_number", read_only=True)
    warehouse_name = serializers.CharField(source="batch.warehouse.name", read_only=True)

    class Meta:
        model = TemperatureObservation
        fields = "__all__"
        read_only_fields = ["excursion", "responsible_user", "created_at"]

    def validate(self, attrs):
        batch = attrs.get("batch", self.instance.batch if self.instance else None)
        validate_tenant(self, batch.program)
        return attrs


class ControlledProductAuthorizationSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    granted_by_name = serializers.CharField(source="granted_by.full_name", read_only=True)

    class Meta:
        model = ControlledProductAuthorization
        fields = "__all__"
        read_only_fields = ["granted_by", "created_at", "updated_at"]

    def validate(self, attrs):
        program, user = attrs.get("program"), attrs.get("user")
        if program and not program.medical_enabled:
            raise serializers.ValidationError({"program": "Medical assistance is disabled for this program."})
        if program:
            validate_tenant(self, program)
        if program and user and user.tenant_id != program.tenant_id:
            raise serializers.ValidationError({"user": "User must belong to the program tenant."})
        return attrs


# Validates medical equipment and returns its current warehouse and custodian.
class MedicalEquipmentSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    responsible_person_name = serializers.CharField(source="responsible_person.full_name", read_only=True)
    condition_display = serializers.CharField(source="get_condition_display", read_only=True)
    availability_display = serializers.CharField(source="get_availability_display", read_only=True)

    class Meta:
        model = MedicalEquipment
        fields = "__all__"
        read_only_fields = ["created_by", "created_at", "updated_at"]

    def validate(self, attrs):
        program = attrs.get("program", getattr(self.instance, "program", None))
        warehouse = attrs.get("warehouse", getattr(self.instance, "warehouse", None))
        product = attrs.get("product", getattr(self.instance, "product", None))
        if not program.medical_enabled or warehouse.program_id != program.id:
            raise serializers.ValidationError("Warehouse must belong to a Medical Assistance-enabled program.")
        validate_tenant(self, program)
        if product and (product.program_id != program.id or product.category != MedicalProduct.Category.EQUIPMENT):
            raise serializers.ValidationError({"product": "Equipment product must belong to the program and use the equipment category."})
        return attrs


# Validates equipment movements and returns readable source and destination data.
class EquipmentMovementSerializer(serializers.ModelSerializer):
    equipment_name = serializers.CharField(source="equipment.name", read_only=True)
    source_warehouse_name = serializers.CharField(source="source_warehouse.name", read_only=True)
    destination_warehouse_name = serializers.CharField(source="destination_warehouse.name", read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.full_name", read_only=True)
    responsible_user_name = serializers.CharField(source="responsible_user.full_name", read_only=True)

    class Meta:
        model = EquipmentMovement
        fields = "__all__"
        read_only_fields = ["responsible_user", "created_at"]

    def validate(self, attrs):
        equipment = attrs.get("equipment", self.instance.equipment if self.instance else None)
        validate_tenant(self, equipment.program)
        return attrs
