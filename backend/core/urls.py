from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    AuditEventViewSet,
    BeneficiaryViewSet,
    BudgetViewSet,
    ComplaintViewSet,
    EnrollmentViewSet,
    HouseholdViewSet,
    PaymentBatchViewSet,
    PaymentInstructionViewSet,
    ProgramActivityViewSet,
    ActivityDependencyViewSet,
    ProgramViewSet,
    ReconciliationItemViewSet,
    ReviewTaskViewSet,
    AdminOnlyTenantUserViewSet,
    SystemTenantViewSet,
    imports_view,
    login_view,
    logout_view,
    me_view,
    pdm_summary_view,
    pdm_view,
    program_summary_view,
    registration_sync_view,
    reports_view,
    trigger_deduplication_api,
    trigger_reconciliation_api,
    trigger_anomaly_detection_api,
    AISignalViewSet,
    AutomationRuleViewSet,
    AutomationExecutionViewSet,
    ai_automation_status_view,
    ai_copilot_view,
    automation_execute_view,
    pipeline_status_view,
    profile_details_view,
    profile_password_view,
    complaint_ai_analyze_view,
    complaint_ai_analysis_view,
    complaint_ai_review_view,
    complaint_ai_analyze_bulk_view,
    HouseholdEligibilityViewSet, HouseholdEnrollmentViewSet, CashEntitlementViewSet,
    WarehouseViewSet, NFIItemViewSet, NFIEntitlementViewSet, StockMovementViewSet,
    DistributionEventViewSet, DistributionAllocationViewSet, DistributionIssueViewSet,
)

router = DefaultRouter()
router.register("programs", ProgramViewSet)
router.register("program-activities", ProgramActivityViewSet)
router.register("activity-dependencies", ActivityDependencyViewSet)
router.register("households", HouseholdViewSet)
router.register("beneficiaries", BeneficiaryViewSet)
router.register("enrollments", EnrollmentViewSet)
router.register("payment-instructions", PaymentInstructionViewSet)
router.register("complaints", ComplaintViewSet)
router.register("budgets", BudgetViewSet)
router.register("audit-events", AuditEventViewSet, basename="audit-event")
router.register("users", AdminOnlyTenantUserViewSet, basename="user")
router.register("tenants", SystemTenantViewSet, basename="tenant")
router.register("payment-batches", PaymentBatchViewSet)
router.register("reconciliation-items", ReconciliationItemViewSet)
router.register("review-tasks", ReviewTaskViewSet)
router.register("ai-signals", AISignalViewSet, basename="ai-signal")
router.register("automation-rules", AutomationRuleViewSet, basename="automation-rule")
router.register("automation-executions", AutomationExecutionViewSet, basename="automation-execution")
router.register("household-eligibility", HouseholdEligibilityViewSet, basename="household-eligibility")
router.register("household-enrollments", HouseholdEnrollmentViewSet, basename="household-enrollment")
router.register("cash-entitlements", CashEntitlementViewSet, basename="cash-entitlement")
router.register("warehouses", WarehouseViewSet, basename="warehouse")
router.register("nfi-items", NFIItemViewSet, basename="nfi-item")
router.register("nfi-entitlements", NFIEntitlementViewSet, basename="nfi-entitlement")
router.register("stock-movements", StockMovementViewSet, basename="stock-movement")
router.register("distribution-events", DistributionEventViewSet, basename="distribution-event")
router.register("distribution-allocations", DistributionAllocationViewSet, basename="distribution-allocation")
router.register("distribution-issues", DistributionIssueViewSet, basename="distribution-issue")

urlpatterns = [
    path("", include(router.urls)),
    path("auth/login/", login_view),
    path("auth/refresh/", TokenRefreshView.as_view()),
    path("auth/logout/", logout_view),
    path("me/", me_view),
    path("profile/details/", profile_details_view),
    path("profile/password/", profile_password_view),
    path("reports/", reports_view),
    path("reporting/programs/<uuid:program_id>/summary/", program_summary_view),
    path("imports/", imports_view),
    path("pdm/", pdm_view),
    path("pdm/summary/", pdm_summary_view),
    path("sync/registrations/", registration_sync_view),
    path("ai/complaints/analyze-bulk/", complaint_ai_analyze_bulk_view, name="complaint-ai-analyze-bulk"),
    path("ai/complaints/<uuid:complaint_id>/analyze/", complaint_ai_analyze_view, name="complaint-ai-analyze"),
    path("ai/complaints/<uuid:complaint_id>/analysis/", complaint_ai_analysis_view, name="complaint-ai-analysis"),
    path("ai/complaints/<uuid:complaint_id>/review/", complaint_ai_review_view, name="complaint-ai-review"),
    
    # مسارات الذكاء الاصطناعي والأتمتة
    path("ai/status/", ai_automation_status_view, name="ai-automation-status"),
    path("ai/deduplicate/<str:beneficiary_id>/", trigger_deduplication_api, name="api-deduplicate"),
    path("ai/reconcile/", trigger_reconciliation_api, name="api-reconcile"),
    path("ai/anomalies/", trigger_anomaly_detection_api, name="api-anomalies"),
    path("ai/copilot/", ai_copilot_view, name="api-copilot"),
    path("ai/automation/execute/", automation_execute_view, name="api-automation-execute"),
    path("pipeline/status/", pipeline_status_view, name="pipeline-status"),
]
