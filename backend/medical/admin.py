from django.contrib import admin

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

for model in (
    MedicalProduct, MedicalBatch, MedicalStockMovement, MedicalEntitlement,
    MedicalDistributionAllocation, MedicalDistributionIssue, MedicalAlert,
    TemperatureObservation, ControlledProductAuthorization, MedicalEquipment,
    EquipmentMovement,
):
    admin.site.register(model)
