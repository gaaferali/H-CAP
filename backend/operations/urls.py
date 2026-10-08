from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    ComplaintViewSet, DistributionAllocationViewSet, DistributionEventViewSet,
    DistributionIssueViewSet, NFIEntitlementViewSet, NFIItemViewSet,
    PaymentDeliveryReviewViewSet, StockMovementViewSet, WarehouseViewSet,
    complaint_ai_analysis_view, complaint_ai_analyze_bulk_view,
    complaint_ai_analyze_view, complaint_ai_review_view,
)

router = SimpleRouter()
router.register("complaints", ComplaintViewSet)
router.register("warehouses", WarehouseViewSet, basename="warehouse")
router.register("nfi-items", NFIItemViewSet, basename="nfi-item")
router.register("nfi-entitlements", NFIEntitlementViewSet, basename="nfi-entitlement")
router.register("stock-movements", StockMovementViewSet, basename="stock-movement")
router.register("distribution-events", DistributionEventViewSet, basename="distribution-event")
router.register("distribution-allocations", DistributionAllocationViewSet, basename="distribution-allocation")
router.register("distribution-issues", DistributionIssueViewSet, basename="distribution-issue")
router.register("payment-delivery-reviews", PaymentDeliveryReviewViewSet, basename="payment-delivery-review")

urlpatterns = [
    path("", include(router.urls)),
    path("ai/complaints/analyze-bulk/", complaint_ai_analyze_bulk_view, name="complaint-ai-analyze-bulk"),
    path("ai/complaints/<uuid:complaint_id>/analyze/", complaint_ai_analyze_view, name="complaint-ai-analyze"),
    path("ai/complaints/<uuid:complaint_id>/analysis/", complaint_ai_analysis_view, name="complaint-ai-analysis"),
    path("ai/complaints/<uuid:complaint_id>/review/", complaint_ai_review_view, name="complaint-ai-review"),
]
