from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import PaymentBatchViewSet, PaymentInstructionViewSet, ReconciliationItemViewSet

router = SimpleRouter()
router.register("payment-batches", PaymentBatchViewSet)
router.register("payment-instructions", PaymentInstructionViewSet)
router.register("reconciliation-items", ReconciliationItemViewSet)
urlpatterns = [path("", include(router.urls))]
