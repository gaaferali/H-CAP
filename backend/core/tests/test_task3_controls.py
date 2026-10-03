from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import (
    AISignal,
    AuditEvent,
    Beneficiary,
    Enrollment,
    PaymentChannelConfig,
    PaymentEvent,
    PaymentInstruction,
    Program,
    ReviewTask,
    SyncOperation,
    Tenant,
    User,
    Household,
)
from core.serializers import PaymentInstructionSerializer
from core.services import (
    calculate_cash_transfer_amount,
    record_offline_event,
    task3_cash_expected_summary,
)
from core.views import PaymentInstructionViewSet
from django.utils import timezone


class Task3TechnicalLeadControlTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="HRI Sandbox",
            tenant_type="NGO",
            default_currency="USD",
        )

        self.user = User.objects.create_user(
            email="amjad.task3@example.test",
            full_name="Amjad Nazar",
            tenant=self.tenant,
            role=User.Role.MANAGER,
            password="test-password",
        )

        self.program = Program.objects.create(
            tenant=self.tenant,
            name="CASH-R1",
            country="Sandbox",
            country_code="SD",
            currency="USD",
            transfer_amount=Decimal("100.00"),
            payment_cycle=Program.Cycle.ONE_TIME,
            status=Program.Status.ACTIVE,
            workflow_config={"module": "CASH"},
            created_by=self.user,
        )

        self.household = Household.objects.create(
            tenant=self.tenant,
            program=self.program,
            household_size=7,
            location="A",
            registration_date=timezone.localdate(),
            client_generated_id="HH001",
            created_by=self.user,
        )

        self.beneficiary = Beneficiary.objects.create(
            household=self.household,
            number="P001",
            full_name="Synthetic Person",
            consent_given=True,
            verification_status=Beneficiary.VerificationStatus.VERIFIED,
            status=Beneficiary.Status.ACTIVE,
            created_by=self.user,
        )

        self.enrollment = Enrollment.objects.create(
            beneficiary=self.beneficiary,
            program=self.program,
            eligibility_status=Enrollment.EligibilityStatus.ELIGIBLE,
            status=Enrollment.Status.APPROVED,
            approved_by=self.user,
            approved_at=timezone.now(),
        )

        self.channel = PaymentChannelConfig.objects.create(
            program=self.program,
            channel_type=PaymentChannelConfig.ChannelType.CASH,
            provider_name="Mock Provider",
            currency="USD",
        )

    def _create_instruction(self, amount="160.00"):
        return PaymentInstruction.objects.create(
            enrollment=self.enrollment,
            beneficiary=self.beneficiary,
            channel_config=self.channel,
            amount=Decimal(amount),
            currency="USD",
            status=PaymentInstruction.Status.CREATED,
            idempotency_key=f"TEST-{timezone.now().timestamp()}",
            created_by=self.user,
        )

    def _simulate(self, instruction, payload):
        factory = APIRequestFactory()
        request = factory.post(
            f"/api/payment-instructions/{instruction.id}/simulate/",
            payload,
            format="json",
        )
        force_authenticate(request, user=self.user)

        view = PaymentInstructionViewSet.as_view(
            {"post": "simulate"}
        )

        return view(
            request,
            pk=str(instruction.id),
        )

    def test_cash_formula_and_cap(self):
        self.assertEqual(
            calculate_cash_transfer_amount(1),
            Decimal("100.00"),
        )
        self.assertEqual(
            calculate_cash_transfer_amount(4),
            Decimal("100.00"),
        )
        self.assertEqual(
            calculate_cash_transfer_amount(5),
            Decimal("120.00"),
        )
        self.assertEqual(
            calculate_cash_transfer_amount(6),
            Decimal("140.00"),
        )
        self.assertEqual(
            calculate_cash_transfer_amount(7),
            Decimal("160.00"),
        )
        self.assertEqual(
            calculate_cash_transfer_amount(99),
            Decimal("160.00"),
        )

    def test_out_of_formula_amount_creates_anomaly_and_is_rejected(self):
        serializer = PaymentInstructionSerializer(
            data={
                "enrollment": str(self.enrollment.id),
                "beneficiary": str(self.beneficiary.id),
                "channel_config": str(self.channel.id),
                "amount": "5000.00",
                "currency": "USD",
            },
            context={
                "request": type(
                    "Request",
                    (),
                    {"user": self.user},
                )()
            },
        )

        with self.assertRaises(ValidationError):
            serializer.is_valid(raise_exception=True)

        self.assertEqual(
            AISignal.objects.filter(
                tenant=self.tenant,
                signal_type="TASK3_ANOMALY",
            ).count(),
            1,
        )

        self.assertEqual(
            ReviewTask.objects.filter(
                tenant=self.tenant,
                task_type=ReviewTask.TaskType.RISK_REVIEW,
            ).count(),
            1,
        )

    def test_offline_replay_is_idempotent_and_payload_mismatch_is_rejected(self):
        payload = {
            "module": "CASH",
            "request_id": "OFFLINE-001",
            "amount": "100.00",
        }

        first = record_offline_event(
            tenant=self.tenant,
            operation_id="OFFLINE-001",
            operation_type="CASH_REQUEST",
            payload=payload,
            actor=self.user,
        )

        second = record_offline_event(
            tenant=self.tenant,
            operation_id="OFFLINE-001",
            operation_type="CASH_REQUEST",
            payload=payload,
            actor=self.user,
        )

        conflict = record_offline_event(
            tenant=self.tenant,
            operation_id="OFFLINE-001",
            operation_type="CASH_REQUEST",
            payload={
                "module": "CASH",
                "request_id": "OFFLINE-001",
                "amount": "999.00",
            },
            actor=self.user,
        )

        self.assertTrue(first["applied"])
        self.assertTrue(second["replay"])
        self.assertEqual(conflict["status"], "conflict")

        self.assertEqual(
            SyncOperation.objects.filter(
                tenant=self.tenant
            ).count(),
            1,
        )

    def test_cash_oracle_matches_task3_baseline(self):
        rows = [
            {
                "row_id": "C01",
                "household_id": "HH001",
                "size": 1,
                "site": "A",
                "consent": "YES",
                "status": "APPROVED",
            },
            {
                "row_id": "C02",
                "household_id": "HH002",
                "size": 2,
                "site": "A",
                "consent": "YES",
                "status": "APPROVED",
            },
            {
                "row_id": "C03",
                "household_id": "HH003",
                "size": 3,
                "site": "A",
                "consent": "YES",
                "status": "APPROVED",
            },
            {
                "row_id": "C04",
                "household_id": "HH004",
                "size": 4,
                "site": "A",
                "consent": "YES",
                "status": "APPROVED",
            },
            {
                "row_id": "C05",
                "household_id": "HH005",
                "size": 5,
                "site": "A",
                "consent": "YES",
                "status": "APPROVED",
            },
            {
                "row_id": "C06",
                "household_id": "HH006",
                "size": 6,
                "site": "A",
                "consent": "YES",
                "status": "APPROVED",
            },
            {
                "row_id": "C07",
                "household_id": "HH007",
                "size": 7,
                "site": "A",
                "consent": "YES",
                "status": "APPROVED",
            },
            {
                "row_id": "C08",
                "household_id": "HH008",
                "size": 8,
                "site": "A",
                "consent": "YES",
                "status": "APPROVED",
            },
            {
                "row_id": "C09",
                "household_id": "HH003",
                "size": 3,
                "site": "A",
                "consent": "YES",
                "status": "APPROVED",
            },
            {
                "row_id": "C10",
                "household_id": "HH010",
                "size": 0,
                "site": "A",
                "consent": "YES",
                "status": "APPROVED",
            },
            {
                "row_id": "C11",
                "household_id": "HH011",
                "size": 4,
                "site": "A",
                "consent": "YES",
                "status": "PENDING",
            },
            {
                "row_id": "C12",
                "household_id": "HH012",
                "size": 2,
                "site": "A",
                "consent": "NO",
                "status": "APPROVED",
            },
        ]

        result = task3_cash_expected_summary(rows)

        self.assertEqual(result["eligible_count"], 8)
        self.assertEqual(result["principal"], "980.00")
        self.assertEqual(result["fees"], "16.00")
        self.assertEqual(result["debit"], "996.00")
        self.assertEqual(len(result["holds"]), 4)

    def test_cas04_duplicate_settled_callback_is_acknowledged_without_double_posting(self):
        instruction = self._create_instruction()

        first = self._simulate(
            instruction,
            {
                "outcome": "success",
                "provider_transaction_id": "PAY-001",
            },
        )

        self.assertEqual(first.status_code, 200)

        instruction.refresh_from_db()

        self.assertEqual(
            instruction.status,
            PaymentInstruction.Status.SUCCESS,
        )
        self.assertEqual(
            instruction.provider_reference,
            "PAY-001",
        )

        event_count_before = PaymentEvent.objects.filter(
            instruction=instruction
        ).count()

        second = self._simulate(
            instruction,
            {
                "outcome": "success",
                "provider_transaction_id": "PAY-001",
            },
        )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            second.data["status"],
            "duplicate_callback",
        )
        self.assertFalse(second.data["double_posting"])

        event_count_after = PaymentEvent.objects.filter(
            instruction=instruction
        ).count()

        self.assertEqual(
            event_count_after,
            event_count_before,
        )

        self.assertTrue(
            AuditEvent.objects.filter(
                tenant=self.tenant,
                action="PAYMENT_CALLBACK_DUPLICATE_ACKNOWLEDGED",
            ).exists()
        )

    def test_cas04_different_provider_reference_cannot_double_settle(self):
        instruction = self._create_instruction()

        first = self._simulate(
            instruction,
            {
                "outcome": "success",
                "provider_transaction_id": "PAY-001",
            },
        )

        self.assertEqual(first.status_code, 200)

        instruction.refresh_from_db()

        second = self._simulate(
            instruction,
            {
                "outcome": "success",
                "provider_transaction_id": "PAY-999",
            },
        )

        self.assertEqual(second.status_code, 400)

        instruction.refresh_from_db()

        self.assertEqual(
            instruction.provider_reference,
            "PAY-001",
        )

        self.assertEqual(
            PaymentEvent.objects.filter(
                instruction=instruction,
                event_type=PaymentEvent.EventType.SUCCESS,
            ).count(),
            1,
        )

    def test_timeout_becomes_unknown_and_retry_is_blocked(self):
        instruction = self._create_instruction()

        response = self._simulate(
            instruction,
            {
                "outcome": "timeout",
                "provider_transaction_id": "PAY-TIMEOUT-001",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "UNKNOWN")
        self.assertTrue(response.data["pending"])
        self.assertFalse(response.data["retry_allowed"])

        instruction.refresh_from_db()

        self.assertEqual(
            instruction.status,
            PaymentInstruction.Status.SUBMITTED,
        )

        self.assertTrue(
            PaymentEvent.objects.filter(
                instruction=instruction,
                provider_status=PaymentEvent.ProviderStatus.UNKNOWN,
            ).exists()
        )

        self.assertTrue(
            AuditEvent.objects.filter(
                tenant=self.tenant,
                action="PAYMENT_TIMEOUT_UNKNOWN",
            ).exists()
        )

        retry_response = self._simulate(
            instruction,
            {
                "outcome": "retry",
            },
        )

        self.assertEqual(retry_response.status_code, 400)

        self.assertEqual(
            PaymentInstruction.objects.filter(
                enrollment=self.enrollment
            ).count(),
            1,
        )

    def test_cas03_failed_payment_retry_creates_new_linked_attempt(self):
        instruction = self._create_instruction()

        failed = self._simulate(
            instruction,
            {
                "outcome": "failure",
                "provider_transaction_id": "PAY-FAILED-001",
            },
        )

        self.assertEqual(failed.status_code, 200)

        instruction.refresh_from_db()

        self.assertEqual(
            instruction.status,
            PaymentInstruction.Status.FAILED,
        )

        retry = self._simulate(
            instruction,
            {
                "outcome": "retry",
            },
        )

        self.assertEqual(retry.status_code, 201)
        self.assertEqual(
            retry.data["status"],
            "retry_created",
        )
        self.assertTrue(
            retry.data["original_failure_retained"]
        )

        original_id = retry.data["original_instruction_id"]
        retry_id = retry.data["retry_instruction_id"]

        self.assertNotEqual(original_id, retry_id)

        original = PaymentInstruction.objects.get(
            id=original_id
        )
        new_attempt = PaymentInstruction.objects.get(
            id=retry_id
        )

        self.assertEqual(
            original.status,
            PaymentInstruction.Status.FAILED,
        )

        self.assertEqual(
            new_attempt.status,
            PaymentInstruction.Status.SUBMITTED,
        )

        self.assertNotEqual(
            original.idempotency_key,
            new_attempt.idempotency_key,
        )

        self.assertTrue(
            PaymentEvent.objects.filter(
                instruction=new_attempt,
                event_type=PaymentEvent.EventType.RETRY,
            ).exists()
        )

        self.assertTrue(
            AuditEvent.objects.filter(
                tenant=self.tenant,
                action="PAYMENT_RETRY_CREATED",
            ).exists()
        )

    def test_x01_same_payment_request_cannot_be_submitted_twice(self):
        instruction = self._create_instruction()

        first = self._simulate(
            instruction,
            {
                "outcome": "submit",
            },
        )

        self.assertEqual(first.status_code, 200)

        instruction.refresh_from_db()

        self.assertEqual(
            instruction.status,
            PaymentInstruction.Status.SUBMITTED,
        )

        second = self._simulate(
            instruction,
            {
                "outcome": "submit",
            },
        )

        self.assertEqual(second.status_code, 400)

        self.assertEqual(
            PaymentEvent.objects.filter(
                instruction=instruction,
                event_type=PaymentEvent.EventType.SUBMITTED,
            ).count(),
            1,
        )

    def test_audit_trail_records_timeout_and_retry_controls(self):
        instruction = self._create_instruction()

        self._simulate(
            instruction,
            {
                "outcome": "failure",
                "provider_transaction_id": "PAY-AUDIT-001",
            },
        )

        self._simulate(
            instruction,
            {
                "outcome": "retry",
            },
        )

        actions = set(
            AuditEvent.objects.filter(
                tenant=self.tenant
            ).values_list(
                "action",
                flat=True,
            )
        )

        self.assertIn(
            "PAYMENT_RETRY_CREATED",
            actions,
        )