from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Tenant, User
from assistance.models import CashEntitlement, Household, HouseholdEligibility, HouseholdModalityDecision
from automation.models import AISignal, ReviewTask
from programs.models import Budget, Program

from .models import PaymentChannelConfig, PaymentInstruction


class CashAnomalyIntegrationTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Cash Tenant", tenant_type="NGO", default_currency="SDG")
        self.user = User.objects.create_user(
            email="cash-admin@example.test", full_name="Cash Admin", tenant=self.tenant,
            role=User.Role.ADMIN, password="test-password",
        )
        self.program = Program.objects.create(
            tenant=self.tenant, name="CASH-R1 response", country="Sudan", country_code="SD",
            currency="SDG", payment_cycle="MONTHLY", cash_enabled=True,
            nfi_enabled=False, medical_enabled=False, workflow_config={"module": "CASH"},
            status=Program.Status.ACTIVE, created_by=self.user,
        )
        Budget.objects.create(program=self.program, currency="SDG", planned_total=Decimal("1000"))
        self.household = Household.objects.create(
            tenant=self.tenant, program=self.program, household_size=5, location="Khartoum",
            registration_date=timezone.localdate(), registration_reference="HH-CASH-1", created_by=self.user,
        )
        HouseholdEligibility.objects.create(
            household=self.household, program=self.program, status=HouseholdEligibility.Status.ELIGIBLE,
            decided_by=self.user, decided_at=timezone.now(),
        )
        HouseholdModalityDecision.objects.create(
            household=self.household, program=self.program, modality=HouseholdModalityDecision.Modality.CASH,
            status=HouseholdModalityDecision.Status.APPROVED, decided_by=self.user, decided_at=timezone.now(),
        )
        self.channel = PaymentChannelConfig.objects.create(
            program=self.program, channel_type=PaymentChannelConfig.ChannelType.CASH,
            provider_name="Simulation", currency="SDG", fee_amount=Decimal("0"), is_active=True,
        )
        self.client.force_authenticate(self.user)

    def test_cash_entitlement_anomaly_persists_review_outside_failed_write(self):
        payload = {
            "program": str(self.program.pk), "household": str(self.household.pk),
            "amount": "110.00", "currency": "SDG", "status": "ACTIVE",
        }
        response = self.client.post(
            reverse("cash-entitlement-list"), payload, format="json", HTTP_IDEMPOTENCY_KEY="wrong-cash-amount",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertEqual(CashEntitlement.objects.count(), 0)
        self.assertEqual(AISignal.objects.filter(signal_type="TASK3_ANOMALY").count(), 1)
        self.assertEqual(ReviewTask.objects.filter(task_type=ReviewTask.TaskType.RISK_REVIEW).count(), 1)
        response = self.client.post(
            reverse("cash-entitlement-list"), payload, format="json", HTTP_IDEMPOTENCY_KEY="wrong-cash-amount",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(AISignal.objects.filter(signal_type="TASK3_ANOMALY").count(), 1)
        self.assertEqual(ReviewTask.objects.filter(task_type=ReviewTask.TaskType.RISK_REVIEW).count(), 1)

    def test_valid_formula_is_accepted_and_payment_endpoint_rechecks_existing_amount(self):
        valid = self.client.post(
            reverse("cash-entitlement-list"), {
                "program": str(self.program.pk), "household": str(self.household.pk),
                "amount": "120.00", "currency": "SDG", "status": "ACTIVE",
            }, format="json", HTTP_IDEMPOTENCY_KEY="correct-cash-amount",
        )
        self.assertEqual(valid.status_code, status.HTTP_201_CREATED, valid.data)

        wrong = CashEntitlement.objects.create(
            household=self.household, program=self.program, amount=Decimal("130"),
            currency="SDG", status=CashEntitlement.Status.ACTIVE, created_by=self.user,
        )
        response = self.client.post(
            reverse("paymentinstruction-list"), {
                "cash_entitlement": str(wrong.pk), "channel_config": str(self.channel.pk),
            }, format="json", HTTP_IDEMPOTENCY_KEY="wrong-payment-amount",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertEqual(PaymentInstruction.objects.count(), 0)
        self.assertTrue(AISignal.objects.filter(entity_type="PAYMENT_INSTRUCTION").exists())
