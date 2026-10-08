from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    AISignalViewSet, AutomationExecutionViewSet, AutomationRuleViewSet,
    ReviewTaskViewSet, ai_automation_status_view, ai_copilot_view,
    automation_execute_view, pipeline_status_view, trigger_anomaly_detection_api,
    trigger_deduplication_api, trigger_reconciliation_api,
)

router = SimpleRouter()
router.register("review-tasks", ReviewTaskViewSet)
router.register("ai-signals", AISignalViewSet, basename="ai-signal")
router.register("automation-rules", AutomationRuleViewSet, basename="automation-rule")
router.register("automation-executions", AutomationExecutionViewSet, basename="automation-execution")

urlpatterns = [
    path("", include(router.urls)),
    path("ai/status/", ai_automation_status_view, name="ai-automation-status"),
    path("ai/deduplicate/<str:beneficiary_id>/", trigger_deduplication_api, name="api-deduplicate"),
    path("ai/reconcile/", trigger_reconciliation_api, name="api-reconcile"),
    path("ai/anomalies/", trigger_anomaly_detection_api, name="api-anomalies"),
    path("ai/copilot/", ai_copilot_view, name="api-copilot"),
    path("ai/automation/execute/", automation_execute_view, name="api-automation-execute"),
    path("pipeline/status/", pipeline_status_view, name="pipeline-status"),
]
