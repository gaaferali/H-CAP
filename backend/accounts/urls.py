from django.urls import include, path
from rest_framework.routers import SimpleRouter
from .views import AdminOnlyTenantUserViewSet, SystemTenantViewSet, login_view, logout_view, me_view, profile_details_view, profile_password_view, refresh_view

router = SimpleRouter()
router.register("users", AdminOnlyTenantUserViewSet, basename="user")
router.register("tenants", SystemTenantViewSet, basename="tenant")
urlpatterns = [
    path("", include(router.urls)), path("auth/login/", login_view),
    path("auth/refresh/", refresh_view), path("auth/logout/", logout_view),
    path("me/", me_view), path("profile/details/", profile_details_view),
    path("profile/password/", profile_password_view),
]
