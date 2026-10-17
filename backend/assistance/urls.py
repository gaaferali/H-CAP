from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import BeneficiaryViewSet, CashEntitlementViewSet, EnrollmentViewSet, HouseholdEligibilityViewSet, HouseholdEnrollmentViewSet, HouseholdModalityDecisionViewSet, HouseholdViewSet

router = SimpleRouter()
router.register("households", HouseholdViewSet)
router.register("beneficiaries", BeneficiaryViewSet)
router.register("enrollments", EnrollmentViewSet)
router.register("household-eligibility", HouseholdEligibilityViewSet, basename="household-eligibility")
router.register("household-enrollments", HouseholdEnrollmentViewSet, basename="household-enrollment")
router.register("household-modality-decisions", HouseholdModalityDecisionViewSet, basename="household-modality-decision")
router.register("cash-entitlements", CashEntitlementViewSet, basename="cash-entitlement")
urlpatterns = [path("", include(router.urls))]
