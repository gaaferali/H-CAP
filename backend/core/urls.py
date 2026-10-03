from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import (
    AISignalViewSet, AdminOnlyTenantUserViewSet, AuditEventViewSet, AutomationExecutionViewSet, AutomationRuleViewSet,
    BeneficiaryViewSet, BudgetViewSet, ComplaintViewSet, EnrollmentViewSet, HouseholdViewSet, PaymentBatchViewSet,
    PaymentInstructionViewSet, ProgramViewSet, ReconciliationItemViewSet, ReviewTaskViewSet, SyncOperationViewSet, ai_closed_view,
    analyze_complaint_api, analyze_complaints_bulk_api, automation_event_api, complaint_ai_analysis_api, complaint_ai_review_api, data_quality_api,
    imports_view, login_view, logout_view, me_view, pdm_summary_view, pdm_view, program_summary_view, registration_sync_view,
    reports_view, trigger_anomaly_detection_api, trigger_deduplication_api, trigger_reconciliation_api, offline_event_api,
)
router = DefaultRouter()
router.register("programs", ProgramViewSet); router.register("households", HouseholdViewSet); router.register("beneficiaries", BeneficiaryViewSet)
router.register("enrollments", EnrollmentViewSet); router.register("payment-instructions", PaymentInstructionViewSet); router.register("complaints", ComplaintViewSet)
router.register("budgets", BudgetViewSet); router.register("audit-events", AuditEventViewSet, basename="audit-event"); router.register("users", AdminOnlyTenantUserViewSet, basename="user")
router.register("payment-batches", PaymentBatchViewSet); router.register("reconciliation-items", ReconciliationItemViewSet); router.register("review-tasks", ReviewTaskViewSet)
router.register("ai-signals", AISignalViewSet, basename="ai-signal"); router.register("automation-rules", AutomationRuleViewSet, basename="automation-rule"); router.register("automation-executions", AutomationExecutionViewSet, basename="automation-execution"); router.register("sync-operations", SyncOperationViewSet, basename="sync-operation")
urlpatterns = [
    path("", include(router.urls)), path("auth/login/", login_view), path("auth/logout/", logout_view), path("me/", me_view), path("reports/", reports_view),
    path("reporting/programs/<uuid:program_id>/summary/", program_summary_view), path("imports/", imports_view), path("pdm/", pdm_view), path("pdm/summary/", pdm_summary_view),
    path("sync/registrations/", registration_sync_view), path("sync/offline-event/", offline_event_api), path("ai/assistant/context/", ai_closed_view), path("ai/assistant/query/", ai_closed_view),
    path("ai/deduplicate/<uuid:beneficiary_id>/", trigger_deduplication_api, name="api-deduplicate"), path("ai/reconcile/", trigger_reconciliation_api, name="api-reconcile"),
    path("ai/anomalies/", trigger_anomaly_detection_api, name="api-anomalies"), path("ai/data-quality/<uuid:beneficiary_id>/", data_quality_api, name="api-data-quality"),
    path("ai/automation/events/", automation_event_api, name="api-automation-event"), path("ai/complaints/<uuid:complaint_id>/analyze/", analyze_complaint_api, name="api-analyze-complaint"),
    path("ai/complaints/<uuid:complaint_id>/analysis/", complaint_ai_analysis_api, name="api-complaint-analysis"), path("ai/complaints/<uuid:complaint_id>/review/", complaint_ai_review_api, name="api-complaint-ai-review"), path("ai/complaints/analyze-bulk/", analyze_complaints_bulk_api, name="api-analyze-complaints-bulk"),
]
