from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

api_root_router = DefaultRouter()

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api_root_router.get_api_root_view(), name="api-root"),
    path("api/", include("accounts.urls")),
    path("api/", include("programs.urls")),
    path("api/", include("assistance.urls")),
    path("api/", include("payments.urls")),
    path("api/", include("operations.urls")),
    path("api/", include("reporting.urls")),
    path("api/", include("automation.urls")),
]
