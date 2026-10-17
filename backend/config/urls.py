from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from django.http import JsonResponse

api_root_router = DefaultRouter()


def health_view(request):
    return JsonResponse({"status": "healthy"})

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api_root_router.get_api_root_view(), name="api-root"),
    path("api/health/", health_view, name="health"),
    path("api/", include("accounts.urls")),
    path("api/", include("programs.urls")),
    path("api/", include("assistance.urls")),
    path("api/", include("payments.urls")),
    path("api/", include("operations.urls")),
    path("api/", include("medical.urls")),
    path("api/", include("reporting.urls")),
    path("api/", include("automation.urls")),
]
