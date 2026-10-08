from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import ActivityDependencyViewSet, BudgetViewSet, ProgramActivityViewSet, ProgramViewSet

router = SimpleRouter()
router.register("programs", ProgramViewSet)
router.register("program-activities", ProgramActivityViewSet)
router.register("activity-dependencies", ActivityDependencyViewSet)
router.register("budgets", BudgetViewSet)
urlpatterns = [path("", include(router.urls))]
