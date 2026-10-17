from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    ControlledProductAuthorizationViewSet,
    EquipmentMovementViewSet,
    MedicalAlertViewSet,
    MedicalBatchViewSet,
    MedicalDistributionAllocationViewSet,
    MedicalDistributionIssueViewSet,
    MedicalEntitlementViewSet,
    MedicalEquipmentViewSet,
    MedicalProductViewSet,
    MedicalStockMovementViewSet,
    TemperatureObservationViewSet,
    medical_dashboard_view,
)

router = SimpleRouter()
router.register("medical-products", MedicalProductViewSet, basename="medical-product")
router.register("medical-batches", MedicalBatchViewSet, basename="medical-batch")
router.register("medical-stock-movements", MedicalStockMovementViewSet, basename="medical-stock-movement")
router.register("medical-entitlements", MedicalEntitlementViewSet, basename="medical-entitlement")
router.register("medical-distribution-allocations", MedicalDistributionAllocationViewSet, basename="medical-distribution-allocation")
router.register("medical-distribution-issues", MedicalDistributionIssueViewSet, basename="medical-distribution-issue")
router.register("medical-alerts", MedicalAlertViewSet, basename="medical-alert")
router.register("medical-temperature-observations", TemperatureObservationViewSet, basename="medical-temperature-observation")
router.register("medical-controlled-authorizations", ControlledProductAuthorizationViewSet, basename="medical-controlled-authorization")
router.register("medical-equipment", MedicalEquipmentViewSet, basename="medical-equipment")
router.register("medical-equipment-movements", EquipmentMovementViewSet, basename="medical-equipment-movement")

urlpatterns = [path("", include(router.urls)), path("medical-dashboard/", medical_dashboard_view, name="medical-dashboard")]
