from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    AuditEventViewSet, imports_view, pdm_summary_view, pdm_view,
    program_summary_view, registration_sync_view, reports_view,
)

router = SimpleRouter()
router.register("audit-events", AuditEventViewSet, basename="audit-event")

urlpatterns = [
    path("", include(router.urls)),
    path("reports/", reports_view),
    path("reporting/programs/<uuid:program_id>/summary/", program_summary_view),
    path("imports/", imports_view),
    path("pdm/", pdm_view),
    path("pdm/summary/", pdm_summary_view),
    path("sync/registrations/", registration_sync_view),
]
