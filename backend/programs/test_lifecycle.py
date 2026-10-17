from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Tenant, User
from assistance.models import CashEntitlement, Household
from operations.models import DistributionEvent, NFIEntitlement, NFIItem, StockMovement, Warehouse
from payments.models import PaymentChannelConfig, PaymentEvent, PaymentInstruction
from programs.models import Program


class SafeLifecycleTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Lifecycle tenant", tenant_type="NGO", default_currency="USD")
        self.manager = User.objects.create_user(
            "lifecycle@example.test", "Lifecycle Manager", self.tenant, "ManagerPass!2026", role=User.Role.MANAGER
        )
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.program = Program.objects.create(
            tenant=self.tenant,
            name="Lifecycle program",
            country="Sudan",
            country_code="SD",
            currency="USD",
            reporting_currency="USD",
            payment_cycle=Program.Cycle.MONTHLY,
            cash_enabled=True,
            nfi_enabled=True,
            created_by=self.manager,
        )

    def action(self, name, reason="Operational lifecycle decision"):
        return self.client.post(f"/api/programs/{self.program.id}/{name}/", {"reason": reason}, format="json")

    def test_program_lifecycle_uses_explicit_audited_transitions(self):
        self.assertEqual(self.action("activate").status_code, 200)
        self.assertEqual(self.action("pause").status_code, 200)
        self.assertEqual(self.action("close").status_code, 200)
        reopened = self.action("reopen")
        self.assertEqual(reopened.status_code, 200, reopened.data)
        self.assertEqual(reopened.data["status"], Program.Status.PAUSED)
        self.assertEqual(reopened.data["status_reason"], "Operational lifecycle decision")

    def test_program_close_is_blocked_by_open_distribution(self):
        self.program.status = Program.Status.ACTIVE
        self.program.save(update_fields=["status"])
        warehouse = Warehouse.objects.create(
            program=self.program, name="Main store", location="Khartoum", created_by=self.manager
        )
        DistributionEvent.objects.create(
            program=self.program,
            warehouse=warehouse,
            location="Khartoum",
            event_date=date.today(),
            distribution_team="Team A",
            created_by=self.manager,
        )
        response = self.action("close")
        self.assertEqual(response.status_code, 400)
        self.assertIn("open_nfi_events", response.data["error"]["fields"]["blockers"])

    def test_household_withdrawal_preserves_successful_payment(self):
        household = Household.objects.create(
            tenant=self.tenant,
            program=self.program,
            household_size=1,
            location="Khartoum",
            registration_date=date.today(),
            created_by=self.manager,
        )
        entitlement = CashEntitlement.objects.create(
            household=household,
            program=self.program,
            amount="25.00",
            currency="USD",
            status=CashEntitlement.Status.PAID,
            created_by=self.manager,
        )
        channel = PaymentChannelConfig.objects.create(
            program=self.program,
            channel_type=PaymentChannelConfig.ChannelType.CASH,
            provider_name="Simulator",
            currency="USD",
        )
        instruction = PaymentInstruction.objects.create(
            cash_entitlement=entitlement,
            channel_config=channel,
            amount="25.00",
            currency="USD",
            status=PaymentInstruction.Status.SUCCESS,
            idempotency_key="withdraw-successful-payment",
            created_by=self.manager,
        )
        response = self.client.post(
            f"/api/households/{household.id}/withdraw/",
            {"reason": "Household requested withdrawal"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        household.refresh_from_db()
        entitlement.refresh_from_db()
        self.assertEqual(household.participation_status, Household.ParticipationStatus.WITHDRAWN)
        self.assertEqual(entitlement.status, CashEntitlement.Status.PAID)
        self.assertTrue(PaymentInstruction.objects.filter(pk=instruction.pk).exists())

    def test_nfi_cancellation_returns_stock_once(self):
        warehouse = Warehouse.objects.create(
            program=self.program, name="NFI store", location="Khartoum", created_by=self.manager
        )
        item = NFIItem.objects.create(
            program=self.program,
            warehouse=warehouse,
            name="Blanket",
            unit="piece",
            initial_quantity=10,
            available_quantity=7,
            created_by=self.manager,
        )
        household = Household.objects.create(
            tenant=self.tenant,
            program=self.program,
            household_size=1,
            location="Omdurman",
            registration_date=date.today(),
            created_by=self.manager,
        )
        entitlement = NFIEntitlement.objects.create(
            household=household,
            program=self.program,
            item=item,
            warehouse=warehouse,
            quantity=3,
            created_by=self.manager,
        )
        first = self.client.post(
            f"/api/nfi-entitlements/{entitlement.id}/cancel/",
            {"reason": "Entitlement entered in error"},
            format="json",
        )
        second = self.client.post(
            f"/api/nfi-entitlements/{entitlement.id}/cancel/",
            {"reason": "Entitlement entered in error"},
            format="json",
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        item.refresh_from_db()
        self.assertEqual(item.available_quantity, 10)
        self.assertEqual(StockMovement.objects.filter(reference=str(entitlement.id), movement_type=StockMovement.MovementType.RETURN).count(), 1)

    def test_cash_cancellation_cancels_only_unsubmitted_instruction(self):
        household = Household.objects.create(
            tenant=self.tenant, program=self.program, household_size=1, location="Khartoum",
            registration_date=date.today(), created_by=self.manager,
        )
        entitlement = CashEntitlement.objects.create(
            household=household, program=self.program, amount="15.00", currency="USD", created_by=self.manager,
        )
        channel = PaymentChannelConfig.objects.create(
            program=self.program, channel_type=PaymentChannelConfig.ChannelType.CASH,
            provider_name="Simulator", currency="USD",
        )
        instruction = PaymentInstruction.objects.create(
            cash_entitlement=entitlement, channel_config=channel, amount="15.00", currency="USD",
            status=PaymentInstruction.Status.CREATED, idempotency_key="cancel-created-payment", created_by=self.manager,
        )
        response = self.client.post(
            f"/api/cash-entitlements/{entitlement.id}/cancel/",
            {"reason": "Duplicate entitlement record"}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        instruction.refresh_from_db()
        self.assertEqual(instruction.status, PaymentInstruction.Status.CANCELLED)
        self.assertEqual(instruction.events.filter(event_type=PaymentEvent.EventType.CANCELLED).count(), 1)
