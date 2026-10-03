from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.serializers.json import DjangoJSONEncoder
from rest_framework.test import APIClient
import json
import uuid
from decimal import Decimal
from datetime import datetime, timezone

from datetime import date

from .models import (
    AISignal,
    AuditEvent,
    AutomationRule,
    Beneficiary,
    Complaint,
    CashEntitlement,
    Enrollment,
    Household,
    HouseholdEligibility,
    HouseholdEnrollment,
    PDMResponse,
    PaymentChannelConfig,
    PaymentBatch,
    PaymentEvent,
    PaymentInstruction,
    Budget,
    Program,
    ProgramActivity,
    ReviewTask,
    Warehouse,
    NFIItem,
    NFIEntitlement,
    DistributionEvent,
    DistributionAllocation,
    DistributionIssue,
    Tenant,
    User,
)
from .views import json_safe


class TenantBoundaryTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", tenant_type="NGO", default_currency="USD")
        self.other_tenant = Tenant.objects.create(name="Tenant B", tenant_type="NGO", default_currency="USD")
        self.manager = User.objects.create_user("manager@example.test", "Tenant Manager", self.tenant, "password", role=User.Role.MANAGER)
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def test_manager_creates_user_only_in_own_tenant(self):
        response = self.client.post("/api/users/", {"email": "field@example.test", "full_name": "Field Officer", "password": "password", "role": User.Role.FIELD_OFFICER, "tenant_id": str(self.other_tenant.id)}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(User.objects.get(email="field@example.test").tenant, self.tenant)

    def test_manager_cannot_create_platform_administrator(self):
        response = self.client.post("/api/users/", {"email": "admin@example.test", "full_name": "Platform Admin", "password": "password", "role": User.Role.ADMIN}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_tenant_administrator_can_create_an_operational_user(self):
        tenant_admin = User.objects.create_user("tenant-admin@example.test", "Tenant Administrator", self.tenant, "password", role=User.Role.ADMIN)
        self.client.force_authenticate(tenant_admin)
        response = self.client.post(
            "/api/users/",
            {"email": "support@example.test", "full_name": "Support Officer", "password": "password", "role": User.Role.SUPPORT},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(User.objects.get(email="support@example.test").tenant, self.tenant)


class ProfileTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", tenant_type="NGO", default_currency="USD")
        self.user = User.objects.create_user("profile@example.test", "Profile User", self.tenant, "CurrentPass123", role=User.Role.FIELD_OFFICER)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_profile_details_require_current_password(self):
        rejected = self.client.post("/api/profile/details/", {"full_name": "Updated User", "email": "updated@example.test", "current_password": "wrong"}, format="json")
        self.assertEqual(rejected.status_code, 400)
        response = self.client.post("/api/profile/details/", {"full_name": "Updated User", "email": "updated@example.test", "current_password": "CurrentPass123"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Updated User")
        self.assertEqual(self.user.email, "updated@example.test")

    def test_password_change_requires_matching_confirmation(self):
        rejected = self.client.post("/api/profile/password/", {"current_password": "CurrentPass123", "new_password": "NewPass123", "confirm_password": "Different123"}, format="json")
        self.assertEqual(rejected.status_code, 400)
        response = self.client.post("/api/profile/password/", {"current_password": "CurrentPass123", "new_password": "NewPass123", "confirm_password": "NewPass123"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPass123"))


class ActivityDependencyTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", tenant_type="NGO", default_currency="USD")
        self.manager = User.objects.create_user("manager@example.test", "Tenant Manager", self.tenant, "password", role=User.Role.MANAGER)
        self.program = Program.objects.create(tenant=self.tenant, name="Cash support", country="Sudan", country_code="SD", currency="USD", reporting_currency="USD", transfer_amount="50.00", payment_cycle=Program.Cycle.MONTHLY, created_by=self.manager)
        self.first = ProgramActivity.objects.create(tenant=self.tenant, program=self.program, name="Register", created_by=self.manager)
        self.second = ProgramActivity.objects.create(tenant=self.tenant, program=self.program, name="Review", created_by=self.manager)
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def test_dependency_cycle_is_rejected(self):
        first_response = self.client.post("/api/activity-dependencies/", {"predecessor": str(self.first.id), "successor": str(self.second.id)}, format="json")
        self.assertEqual(first_response.status_code, 201)
        cycle_response = self.client.post("/api/activity-dependencies/", {"predecessor": str(self.second.id), "successor": str(self.first.id)}, format="json")
        self.assertEqual(cycle_response.status_code, 400)


class DuplicateEndpointTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", tenant_type="NGO", default_currency="USD")
        self.reviewer = User.objects.create_user("reviewer@example.test", "Reviewer", self.tenant, "password", role=User.Role.REVIEWER)
        self.client = APIClient()
        self.client.force_authenticate(self.reviewer)

    def test_invalid_beneficiary_identifier_returns_validation_error(self):
        response = self.client.post("/api/ai/deduplicate/27/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("valid beneficiary", response.data["error"]["message"])


class AutomationExecutionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", tenant_type="NGO", default_currency="USD")
        self.manager = User.objects.create_user("manager@example.test", "Manager", self.tenant, "password", role=User.Role.MANAGER)
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def create_rule(self, action_name):
        response = self.client.post(
            "/api/automation-rules/",
            {"event_name": "DUPLICATE_FLAG", "action_name": action_name, "is_active": True, "priority": 100},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        return response.data["id"]

    def test_decision_sensitive_action_is_recorded_as_dry_run(self):
        rule_id = self.create_rule("RETRY")
        response = self.client.post(
            "/api/ai/automation/execute/",
            {"rule_id": rule_id, "idempotency_key": "dry-run-test-1", "event_payload": {}},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "DRY_RUN")
        self.assertEqual(response.data["outcome"], "DRY_RUN_NOT_EXECUTED")
        self.assertFalse(response.data["review_task_created"])
        self.assertEqual(ReviewTask.objects.count(), 0)

    def test_review_task_action_creates_human_queue_item(self):
        rule_id = self.create_rule("CREATE_REVIEW_TASK")
        response = self.client.post(
            "/api/ai/automation/execute/",
            {
                "rule_id": rule_id,
                "idempotency_key": "review-task-test-1",
                "event_payload": {"task_type": "DUPLICATE_REVIEW", "priority": "HIGH"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "COMPLETED")
        self.assertEqual(response.data["outcome"], "HUMAN_REVIEW_TASK_CREATED")
        self.assertTrue(response.data["review_task_created"])
        task = ReviewTask.objects.get()
        self.assertEqual(task.task_type, ReviewTask.TaskType.DUPLICATE_REVIEW)
        self.assertEqual(task.priority, ReviewTask.Priority.HIGH)
        self.assertIsNone(task.assigned_to)


class RegistrationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", tenant_type="NGO", default_currency="USD")
        self.other_tenant = Tenant.objects.create(name="Tenant B", tenant_type="NGO", default_currency="USD")
        self.field_officer = User.objects.create_user("field@example.test", "Field Officer", self.tenant, "password", role=User.Role.FIELD_OFFICER)
        self.tenant_admin = User.objects.create_user("admin@example.test", "Tenant Admin", self.tenant, "password", role=User.Role.ADMIN)
        self.support = User.objects.create_user("support@example.test", "Support Officer", self.tenant, "password", role=User.Role.SUPPORT)
        self.manager = User.objects.create_user("manager@example.test", "Tenant Manager", self.tenant, "password", role=User.Role.MANAGER)
        self.program = Program.objects.create(tenant=self.tenant, name="Cash support", country="Sudan", country_code="SD", currency="USD", reporting_currency="USD", transfer_amount="50.00", payment_cycle=Program.Cycle.MONTHLY, created_by=self.manager)
        self.other_program = Program.objects.create(tenant=self.other_tenant, name="Other support", country="Chad", country_code="TD", currency="USD", reporting_currency="USD", transfer_amount="50.00", payment_cycle=Program.Cycle.MONTHLY, created_by=User.objects.create_user("other-manager@example.test", "Other Manager", self.other_tenant, "password", role=User.Role.MANAGER))
        self.client = APIClient()

    def create_household(self, user):
        self.client.force_authenticate(user)
        return self.client.post("/api/households/", {"program": str(self.program.id), "household_size": 4, "location": "Khartoum", "registration_date": "2026-09-25"}, format="json")

    def test_field_officer_creates_household_without_client_generated_id(self):
        response = self.create_household(self.field_officer)
        self.assertEqual(response.status_code, 201)
        household = Household.objects.get(id=response.data["id"])
        self.assertEqual(household.client_generated_id, "")
        self.assertEqual(household.tenant, self.tenant)
        self.assertEqual(household.created_by, self.field_officer)
        self.assertTrue(response.data["registration_reference"].startswith("HH-"))

    def test_tenant_admin_creates_household_without_client_generated_id(self):
        response = self.create_household(self.tenant_admin)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Household.objects.get(id=response.data["id"]).created_by, self.tenant_admin)

    def test_unauthorized_role_cannot_create_household(self):
        response = self.create_household(self.support)
        self.assertEqual(response.status_code, 403)

    def test_beneficiary_saves_full_phone_and_hides_raw_national_id(self):
        household_response = self.create_household(self.field_officer)
        response = self.client.post("/api/beneficiaries/", {"household": household_response.data["id"], "national_id": "AB-123 456", "full_name": "Amina Ahmed", "phone_number": "+249 91 234-5678", "consent_given": True}, format="json")
        self.assertEqual(response.status_code, 201)
        beneficiary = Beneficiary.objects.get(id=response.data["id"])
        self.assertEqual(beneficiary.phone_number, "+249912345678")
        self.assertEqual(beneficiary.phone_last4, "5678")
        self.assertEqual(response.data["masked_phone"], "**** 5678")
        self.assertTrue(beneficiary.national_id_hash)
        self.assertNotEqual(beneficiary.number, "AB-123 456")
        self.assertNotIn("number", response.data)
        self.assertNotIn("national_id", response.data)
        self.assertNotIn("AB-123 456", str(response.data))

    def test_cross_tenant_household_cannot_receive_beneficiary(self):
        other_household = Household.objects.create(tenant=self.other_tenant, program=self.other_program, household_size=2, location="N'Djamena", registration_date=date(2026, 9, 25), created_by=self.other_program.created_by)
        self.client.force_authenticate(self.field_officer)
        response = self.client.post("/api/beneficiaries/", {"household": str(other_household.id), "national_id": "TD-12345", "full_name": "Other Person"}, format="json")
        self.assertEqual(response.status_code, 400)


class PlatformAdministrationTests(TestCase):
    def setUp(self):
        self.system_tenant = Tenant.objects.create(name="System", tenant_type="SYSTEM", default_currency="USD")
        self.admin = User.objects.create_superuser("admin@example.test", "Platform Admin", "password", tenant=self.system_tenant)
        self.manager = User.objects.create_user("manager@example.test", "Tenant Manager", self.system_tenant, "password", role=User.Role.MANAGER)
        self.auditor = User.objects.create_user("auditor@example.test", "Auditor", self.system_tenant, "password", role=User.Role.AUDITOR)
        self.client = APIClient()

    def test_platform_administrator_creates_tenant(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/tenants/", {"name": "New Partner", "tenant_type": "NGO", "default_currency": "USD", "is_active": True}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Tenant.objects.filter(name="New Partner").exists())
        self.assertTrue(AuditEvent.objects.filter(action="TENANT_CREATED").exists())

    def test_tenant_audit_roles_can_read_only_their_tenant_history(self):
        AuditEvent.objects.create(tenant=self.system_tenant, actor=self.admin, action="TEST", entity_type="Tenant", entity_id=str(self.system_tenant.id))
        self.client.force_authenticate(self.manager)
        manager_response = self.client.get("/api/audit-events/")
        self.assertEqual(manager_response.status_code, 200)
        self.assertEqual(len(manager_response.data.get("results", manager_response.data)), 1)
        self.client.force_authenticate(self.auditor)
        self.assertEqual(self.client.get("/api/audit-events/").status_code, 200)


class ProgramAccessTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", tenant_type="NGO", default_currency="USD")
        self.manager = User.objects.create_user("manager@example.test", "Tenant Manager", self.tenant, "password", role=User.Role.MANAGER)
        self.finance = User.objects.create_user("finance@example.test", "Finance Officer", self.tenant, "password", role=User.Role.FINANCE)
        self.field_officer = User.objects.create_user("field@example.test", "Field Officer", self.tenant, "password", role=User.Role.FIELD_OFFICER)
        self.reviewer = User.objects.create_user("reviewer@example.test", "Reviewer", self.tenant, "password", role=User.Role.REVIEWER)
        self.auditor = User.objects.create_user("auditor@example.test", "Auditor", self.tenant, "password", role=User.Role.AUDITOR)
        self.program = Program.objects.create(
            tenant=self.tenant,
            name="Cash support",
            country="Sudan",
            country_code="SD",
            currency="USD",
            reporting_currency="USD",
            transfer_amount="50.00",
            payment_cycle=Program.Cycle.MONTHLY,
            created_by=self.manager,
        )
        self.client = APIClient()

    def test_operational_roles_can_read_tenant_programs(self):
        for user in [self.finance, self.field_officer, self.auditor]:
            with self.subTest(role=user.role):
                self.client.force_authenticate(user)
                response = self.client.get("/api/programs/")
                self.assertEqual(response.status_code, 200)
                programs = response.data.get("results", response.data)
                self.assertEqual(programs[0]["id"], str(self.program.id))

    def test_finance_cannot_create_program_but_can_configure_channel(self):
        self.client.force_authenticate(self.finance)
        create_response = self.client.post("/api/programs/", {}, format="json")
        self.assertEqual(create_response.status_code, 403)
        channel_response = self.client.post(
            f"/api/programs/{self.program.id}/channels/",
            {"channel_type": "MOBILE_MONEY", "provider_name": "Simulated wallet", "currency": "USD", "is_active": True},
            format="json",
        )
        self.assertEqual(channel_response.status_code, 201)

    def test_payment_channels_require_cash_enabled_program(self):
        nfi_only = Program.objects.create(
            tenant=self.tenant,
            name="NFI only",
            country="Sudan",
            currency="USD",
            cash_enabled=False,
            nfi_enabled=True,
            created_by=self.manager,
        )
        combined = Program.objects.create(
            tenant=self.tenant,
            name="Cash and NFI",
            country="Sudan",
            currency="USD",
            transfer_amount="25.00",
            payment_cycle=Program.Cycle.ONE_TIME,
            cash_enabled=True,
            nfi_enabled=True,
            created_by=self.manager,
        )
        self.client.force_authenticate(self.finance)
        rejected = self.client.post(
            f"/api/programs/{nfi_only.id}/channels/",
            {"channel_type": "CASH", "provider_name": "Simulator", "currency": "USD", "is_active": True},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400)
        accepted = self.client.post(
            f"/api/programs/{combined.id}/channels/",
            {"channel_type": "CASH", "provider_name": "Simulator", "currency": "USD", "is_active": True},
            format="json",
            HTTP_IDEMPOTENCY_KEY="combined-channel-1",
        )
        replay = self.client.post(
            f"/api/programs/{combined.id}/channels/",
            {"channel_type": "CASH", "provider_name": "Simulator", "currency": "USD", "is_active": True},
            format="json",
            HTTP_IDEMPOTENCY_KEY="combined-channel-1",
        )
        self.assertEqual(accepted.status_code, 201, accepted.data)
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(PaymentChannelConfig.objects.filter(program=combined).count(), 1)

    def test_reviewer_cannot_create_enrollment_for_another_tenant(self):
        other_tenant = Tenant.objects.create(name="Tenant B", tenant_type="NGO", default_currency="USD")
        other_manager = User.objects.create_user("other-manager@example.test", "Other Manager", other_tenant, "password", role=User.Role.MANAGER)
        other_program = Program.objects.create(
            tenant=other_tenant,
            name="Other cash support",
            country="Chad",
            country_code="TD",
            currency="USD",
            reporting_currency="USD",
            transfer_amount="50.00",
            payment_cycle=Program.Cycle.MONTHLY,
            created_by=other_manager,
        )
        household = Household.objects.create(
            tenant=other_tenant,
            program=other_program,
            household_size=2,
            location="N'Djamena",
            registration_date=date(2026, 9, 25),
            created_by=other_manager,
        )
        beneficiary = Beneficiary.objects.create(
            household=household,
            number="OTHER-001",
            full_name="Other Tenant Beneficiary",
            created_by=other_manager,
        )
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            "/api/enrollments/",
            {"program": str(other_program.id), "beneficiary": str(beneficiary.id), "eligibility_status": "ELIGIBLE", "status": "ENROLLED"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class PaymentInstructionWorkflowTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Payments Tenant", tenant_type="NGO", default_currency="USD")
        self.manager = User.objects.create_user("payment-manager@example.test", "Payment Manager", self.tenant, "password", role=User.Role.MANAGER)
        self.finance = User.objects.create_user("payment-finance@example.test", "Payment Finance", self.tenant, "password", role=User.Role.FINANCE)
        self.reviewer = User.objects.create_user("payment-reviewer@example.test", "Payment Reviewer", self.tenant, "password", role=User.Role.REVIEWER)
        self.program = Program.objects.create(
            tenant=self.tenant,
            name="Household payments",
            country="Sudan",
            currency="USD",
            transfer_amount="50.00",
            payment_cycle=Program.Cycle.ONE_TIME,
            created_by=self.manager,
        )
        self.budget = Budget.objects.create(program=self.program, currency="USD", planned_total="500.00")
        self.household = Household.objects.create(
            tenant=self.tenant,
            program=self.program,
            household_size=2,
            location="Khartoum",
            registration_date=date.today(),
            created_by=self.manager,
        )
        self.beneficiary = Beneficiary.objects.create(
            household=self.household,
            number="PAY-001",
            full_name="Payment Beneficiary",
            created_by=self.manager,
        )
        self.household_enrollment = HouseholdEnrollment.objects.create(
            household=self.household,
            program=self.program,
            status=HouseholdEnrollment.Status.APPROVED,
            assistance_modality=Enrollment.AssistanceModality.CASH,
            decided_by=self.reviewer,
        )
        self.eligibility = HouseholdEligibility.objects.create(
            household=self.household,
            program=self.program,
            status=HouseholdEligibility.Status.ELIGIBLE,
            decided_by=self.reviewer,
        )
        self.entitlement = CashEntitlement.objects.create(
            household=self.household,
            beneficiary=self.beneficiary,
            program=self.program,
            amount="50.00",
            currency="USD",
            created_by=self.finance,
        )
        self.channel = PaymentChannelConfig.objects.create(
            program=self.program,
            channel_type=PaymentChannelConfig.ChannelType.MOBILE_MONEY,
            provider_name="Payment simulator",
            currency="USD",
        )
        self.other_program = Program.objects.create(
            tenant=self.tenant,
            name="Other payment program",
            country="Sudan",
            currency="USD",
            transfer_amount="50.00",
            payment_cycle=Program.Cycle.ONE_TIME,
            created_by=self.manager,
        )
        self.other_channel = PaymentChannelConfig.objects.create(
            program=self.other_program,
            channel_type=PaymentChannelConfig.ChannelType.MOBILE_MONEY,
            provider_name="Other simulator",
            currency="USD",
        )
        self.client = APIClient()

    def instruction_payload(self, **overrides):
        payload = {
            "cash_entitlement": str(self.entitlement.id),
            "channel_config": str(self.channel.id),
            "amount": "50.00",
            "currency": "USD",
        }
        payload.update(overrides)
        return payload

    def test_new_instruction_uses_household_entitlement_and_exact_amount(self):
        self.client.force_authenticate(self.finance)
        response = self.client.post(
            "/api/payment-instructions/",
            self.instruction_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        instruction = PaymentInstruction.objects.get(pk=response.data["id"])
        self.assertEqual(instruction.cash_entitlement, self.entitlement)
        self.assertIsNone(instruction.enrollment)
        self.assertEqual(instruction.beneficiary, self.beneficiary)
        self.assertEqual(response.data["program_name"], self.program.name)
        self.assertEqual(
            response.data["household_reference"],
            self.household.registration_reference or f"HH-{str(self.household.id).split('-')[0].upper()}",
        )
        self.assertEqual(response.data["household_member_count"], 1)
        self.assertEqual(response.data["household_size"], 2)
        self.assertEqual(response.data["household_members"][0]["full_name"], self.beneficiary.full_name)
        duplicate = self.client.post(
            "/api/payment-instructions/",
            self.instruction_payload(),
            format="json",
        )
        self.assertEqual(duplicate.status_code, 400, duplicate.data)

    def test_payment_instruction_remains_household_level_without_beneficiary_assignment(self):
        self.entitlement.beneficiary = None
        self.entitlement.save(update_fields=["beneficiary"])
        self.client.force_authenticate(self.finance)

        response = self.client.post(
            "/api/payment-instructions/",
            self.instruction_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        instruction = PaymentInstruction.objects.get(pk=response.data["id"])
        self.assertEqual(instruction.cash_entitlement, self.entitlement)
        self.assertIsNone(instruction.beneficiary)
        self.assertEqual(response.data["household_member_count"], 1)
        self.assertEqual(response.data["household_members"][0]["full_name"], self.beneficiary.full_name)

    def test_eligible_approved_cash_and_cash_nfi_households_can_create_payment_instructions(self):
        self.program.nfi_enabled = True
        self.program.save(update_fields=["nfi_enabled"])
        cash_nfi_household = Household.objects.create(
            tenant=self.tenant,
            program=self.program,
            household_size=4,
            location="Omdurman",
            registration_date=date.today(),
            created_by=self.manager,
        )
        HouseholdEligibility.objects.create(
            household=cash_nfi_household,
            program=self.program,
            status=HouseholdEligibility.Status.ELIGIBLE,
            decided_by=self.reviewer,
        )
        HouseholdEnrollment.objects.create(
            household=cash_nfi_household,
            program=self.program,
            status=HouseholdEnrollment.Status.APPROVED,
            assistance_modality=Enrollment.AssistanceModality.CASH_NFI,
            decided_by=self.reviewer,
        )
        self.client.force_authenticate(self.finance)

        enrollment_list = self.client.get("/api/household-enrollments/")
        self.assertEqual(enrollment_list.status_code, 200, enrollment_list.data)
        eligible_rows = [
            row
            for row in enrollment_list.data["results"]
            if str(row["household"]) in {
                str(self.household.id),
                str(cash_nfi_household.id),
            }
        ]
        self.assertEqual(len(eligible_rows), 2)
        self.assertTrue(all(row["eligibility_status"] == "ELIGIBLE" for row in eligible_rows))
        self.assertTrue(all(row["status"] == "APPROVED" for row in eligible_rows))
        self.assertEqual(
            {row["assistance_modality"] for row in eligible_rows},
            {"CASH", "CASH_NFI"},
        )

        created_instructions = []
        for household in (self.household, cash_nfi_household):
            response = self.client.post(
                "/api/payment-instructions/",
                {
                    "household": str(household.id),
                    "channel_config": str(self.channel.id),
                    "amount": "50.00",
                    "currency": "USD",
                },
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.data)
            instruction = PaymentInstruction.objects.get(pk=response.data["id"])
            self.assertEqual(instruction.cash_entitlement.household, household)
            self.assertIsNone(instruction.beneficiary)
            self.assertIsNone(instruction.enrollment)
            self.assertEqual(instruction.amount, Decimal("50.00"))
            self.assertEqual(instruction.currency, self.program.currency)
            created_instructions.append(instruction)
        self.assertEqual(len(created_instructions), 2)
        self.assertEqual(
            CashEntitlement.objects.filter(
                household=cash_nfi_household,
                beneficiary__isnull=True,
            ).count(),
            1,
        )

    def test_instruction_rejects_legacy_only_wrong_amount_and_cross_program_channel(self):
        self.client.force_authenticate(self.finance)
        legacy = Enrollment.objects.create(
            beneficiary=self.beneficiary,
            household=self.household,
            program=self.program,
            eligibility_status=Enrollment.EligibilityStatus.ELIGIBLE,
            status=Enrollment.Status.APPROVED,
            assistance_modality=Enrollment.AssistanceModality.CASH,
        )
        legacy_only = self.client.post(
            "/api/payment-instructions/",
            {"enrollment": str(legacy.id), "beneficiary": str(self.beneficiary.id), "channel_config": str(self.channel.id), "amount": "50.00", "currency": "USD"},
            format="json",
        )
        wrong_amount = self.client.post(
            "/api/payment-instructions/",
            self.instruction_payload(amount="49.99"),
            format="json",
        )
        wrong_currency = self.client.post(
            "/api/payment-instructions/",
            self.instruction_payload(currency="EUR"),
            format="json",
        )
        wrong_channel = self.client.post(
            "/api/payment-instructions/",
            self.instruction_payload(channel_config=str(self.other_channel.id)),
            format="json",
        )

        self.assertEqual(legacy_only.status_code, 400, legacy_only.data)
        self.assertEqual(wrong_amount.status_code, 400, wrong_amount.data)
        self.assertEqual(wrong_currency.status_code, 400, wrong_currency.data)
        self.assertEqual(wrong_channel.status_code, 400, wrong_channel.data)
        self.assertEqual(PaymentInstruction.objects.count(), 0)

    def test_batch_must_match_entitlement_program_and_tenant(self):
        other_tenant = Tenant.objects.create(name="Other payments tenant", tenant_type="NGO", default_currency="USD")
        other_manager = User.objects.create_user("other-payment-manager@example.test", "Other Manager", other_tenant, "password", role=User.Role.MANAGER)
        foreign_program = Program.objects.create(
            tenant=other_tenant,
            name="Foreign payment program",
            country="Chad",
            currency="USD",
            transfer_amount="50.00",
            payment_cycle=Program.Cycle.ONE_TIME,
            created_by=other_manager,
        )
        foreign_batch = PaymentBatch.objects.create(
            tenant=other_tenant,
            program=foreign_program,
            name="Foreign batch",
            idempotency_key="foreign-payment-batch",
            created_by=other_manager,
        )
        self.client.force_authenticate(self.finance)

        response = self.client.post(
            "/api/payment-instructions/",
            self.instruction_payload(batch=str(foreign_batch.id)),
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(PaymentInstruction.objects.count(), 0)

    def test_instruction_rejects_an_entitlement_from_another_tenant(self):
        other_tenant = Tenant.objects.create(name="Foreign entitlement tenant", tenant_type="NGO", default_currency="USD")
        other_manager = User.objects.create_user("foreign-payment-manager@example.test", "Foreign Manager", other_tenant, "password", role=User.Role.MANAGER)
        foreign_program = Program.objects.create(
            tenant=other_tenant,
            name="Foreign payment program",
            country="Chad",
            currency="USD",
            transfer_amount="50.00",
            payment_cycle=Program.Cycle.ONE_TIME,
            created_by=other_manager,
        )
        foreign_household = Household.objects.create(
            tenant=other_tenant,
            program=foreign_program,
            household_size=1,
            location="N'Djamena",
            registration_date=date.today(),
            created_by=other_manager,
        )
        HouseholdEnrollment.objects.create(
            household=foreign_household,
            program=foreign_program,
            status=HouseholdEnrollment.Status.APPROVED,
            assistance_modality=Enrollment.AssistanceModality.CASH,
        )
        foreign_entitlement = CashEntitlement.objects.create(
            household=foreign_household,
            program=foreign_program,
            amount="50.00",
            currency="USD",
            created_by=other_manager,
        )
        foreign_channel = PaymentChannelConfig.objects.create(
            program=foreign_program,
            channel_type=PaymentChannelConfig.ChannelType.MOBILE_MONEY,
            provider_name="Foreign simulator",
            currency="USD",
        )
        self.client.force_authenticate(self.finance)

        response = self.client.post(
            "/api/payment-instructions/",
            self.instruction_payload(
                cash_entitlement=str(foreign_entitlement.id),
                channel_config=str(foreign_channel.id),
            ),
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(PaymentInstruction.objects.count(), 0)

    def test_reviewer_can_read_payments_but_cannot_create_or_change_them(self):
        instruction = PaymentInstruction.objects.create(
            cash_entitlement=self.entitlement,
            beneficiary=self.beneficiary,
            channel_config=self.channel,
            amount="50.00",
            currency="USD",
            idempotency_key="existing-household-payment",
            created_by=self.finance,
        )
        self.client.force_authenticate(self.reviewer)

        listed = self.client.get("/api/payment-instructions/")
        entitlements = self.client.get("/api/cash-entitlements/")
        enrollments = self.client.get("/api/household-enrollments/")
        create = self.client.post(
            "/api/payment-instructions/",
            self.instruction_payload(),
            format="json",
        )
        update = self.client.patch(
            f"/api/payment-instructions/{instruction.id}/",
            {"amount": "1.00"},
            format="json",
        )

        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(entitlements.status_code, 200, entitlements.data)
        self.assertEqual(enrollments.status_code, 200, enrollments.data)
        self.assertEqual(create.status_code, 403, create.data)
        self.assertEqual(update.status_code, 403, update.data)

    def test_finance_can_read_but_cannot_create_or_update_household_enrollment(self):
        HouseholdEligibility.objects.create(
            household=self.household,
            program=self.program,
            status=HouseholdEligibility.Status.ELIGIBLE,
        )
        pending_household = Household.objects.create(
            tenant=self.tenant,
            program=self.program,
            household_size=1,
            location="Pending enrollment",
            registration_date=date.today(),
            created_by=self.manager,
        )
        pending_enrollment = HouseholdEnrollment.objects.create(
            household=pending_household,
            program=self.program,
            status=HouseholdEnrollment.Status.PENDING,
            assistance_modality=Enrollment.AssistanceModality.CASH,
        )
        self.client.force_authenticate(self.finance)

        enrollments = self.client.get("/api/household-enrollments/")
        eligibility = self.client.get("/api/household-eligibility/")
        nfi_entitlements = self.client.get("/api/nfi-entitlements/")
        households = self.client.get("/api/households/")
        beneficiaries = self.client.get("/api/beneficiaries/")
        create_enrollment = self.client.post(
            "/api/household-enrollments/",
            {"household": str(self.household.id), "program": str(self.program.id), "status": "ENROLLED", "assistance_modality": "CASH"},
            format="json",
        )

        self.assertEqual(enrollments.status_code, 200, enrollments.data)
        self.assertIn(
            str(self.household_enrollment.id),
            [row["id"] for row in enrollments.data["results"]],
        )
        self.assertNotIn(
            str(pending_enrollment.id),
            [row["id"] for row in enrollments.data["results"]],
        )
        self.assertEqual(eligibility.status_code, 403, eligibility.data)
        self.assertEqual(nfi_entitlements.status_code, 200, nfi_entitlements.data)
        self.assertEqual(households.status_code, 200, households.data)
        self.assertEqual(beneficiaries.status_code, 200, beneficiaries.data)
        self.assertEqual(create_enrollment.status_code, 403, create_enrollment.data)


class PDMAndAuthenticationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", tenant_type="NGO", default_currency="USD")
        self.other_tenant = Tenant.objects.create(name="Tenant B", tenant_type="NGO", default_currency="USD")
        self.field_officer = User.objects.create_user("field@example.test", "Field Officer", self.tenant, "password", role=User.Role.FIELD_OFFICER)
        self.manager = User.objects.create_user("manager@example.test", "Tenant Manager", self.tenant, "password", role=User.Role.MANAGER)
        self.program = Program.objects.create(
            tenant=self.tenant,
            name="Cash support",
            country="Sudan",
            country_code="SD",
            currency="USD",
            reporting_currency="USD",
            transfer_amount="50.00",
            payment_cycle=Program.Cycle.MONTHLY,
            created_by=self.manager,
        )
        self.client = APIClient()

    def create_successful_payment(self):
        household = Household.objects.create(
            tenant=self.tenant,
            program=self.program,
            household_size=1,
            location="North",
            registration_date=date.today(),
            created_by=self.field_officer,
        )
        beneficiary = Beneficiary.objects.create(
            household=household,
            number="PDM-001",
            full_name="PDM Beneficiary",
            consent_given=True,
            created_by=self.field_officer,
        )
        enrollment = Enrollment.objects.create(
            beneficiary=beneficiary,
            program=self.program,
            eligibility_status=Enrollment.EligibilityStatus.ELIGIBLE,
            status=Enrollment.Status.APPROVED,
            approved_by=self.manager,
        )
        channel = PaymentChannelConfig.objects.create(
            program=self.program,
            channel_type=PaymentChannelConfig.ChannelType.MOBILE_MONEY,
            provider_name="Simulator",
            currency="USD",
        )
        PaymentInstruction.objects.create(
            enrollment=enrollment,
            beneficiary=beneficiary,
            channel_config=channel,
            amount="50.00",
            currency="USD",
            status=PaymentInstruction.Status.SUCCESS,
            idempotency_key="pdm-success-payment",
            created_by=self.manager,
        )
        return beneficiary

    def test_jwt_login_can_call_current_user_endpoint(self):
        login_response = self.client.post(
            "/api/auth/login/",
            {"email": self.field_officer.email, "password": "password"},
            format="json",
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn("access", login_response.data)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_response.data['access']}")
        me_response = self.client.get("/api/me/")
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.data["email"], self.field_officer.email)

    def test_jwt_logout_blacklists_the_refresh_token(self):
        login_response = self.client.post(
            "/api/auth/login/",
            {"email": self.field_officer.email, "password": "password"},
            format="json",
        )
        self.assertEqual(login_response.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_response.data['access']}")
        logout_response = self.client.post(
            "/api/auth/logout/",
            {"refresh": login_response.data["refresh"]},
            format="json",
        )
        self.assertEqual(logout_response.status_code, 200)
        refresh_response = self.client.post(
            "/api/auth/refresh/",
            {"refresh": login_response.data["refresh"]},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 401)

    def test_field_officer_pdm_uses_successful_payment_and_complaint_data(self):
        beneficiary = self.create_successful_payment()
        Complaint.objects.create(
            beneficiary=beneficiary,
            category="Access",
            description="Assistance was delayed.",
            created_by=self.manager,
        )
        self.client.force_authenticate(self.field_officer)
        response = self.client.post(
            "/api/pdm/",
            {"program": str(self.program.id), "received_count": 1, "access_problem_rate": 2, "satisfaction": 95},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        pdm_response = PDMResponse.objects.get(id=response.data["id"])
        self.assertEqual(pdm_response.tenant, self.tenant)
        self.assertEqual(pdm_response.received_count, 1)
        self.assertEqual(str(pdm_response.amount_received), "50.00")
        self.assertEqual(str(pdm_response.complaint_rate), "100.00")
        summary = self.client.get(f"/api/pdm/summary/?program={self.program.id}")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.data["paid_beneficiaries"], 1)
        self.assertEqual(summary.data["recorded_recipients"], 1)
        self.assertEqual(summary.data["remaining_recipients"], 0)
        self.assertEqual(summary.data["complaints"], 1)

    def test_support_pdm_access_is_summary_only(self):
        support = User.objects.create_user("support@example.test", "Support", self.tenant, "password", role=User.Role.SUPPORT)
        self.client.force_authenticate(support)
        self.assertEqual(self.client.get("/api/pdm/summary/").status_code, 200)
        response = self.client.post("/api/pdm/", {"program": str(self.program.id), "received_count": 1}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_reviewer_can_view_beneficiaries_but_cannot_register_them(self):
        reviewer = User.objects.create_user("reviewer@example.test", "Reviewer", self.tenant, "password", role=User.Role.REVIEWER)
        self.client.force_authenticate(reviewer)
        self.assertEqual(self.client.get("/api/beneficiaries/").status_code, 200)
        response = self.client.post("/api/beneficiaries/", {}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_reviewer_can_update_existing_eligibility_status(self):
        reviewer = User.objects.create_user("eligibility-reviewer@example.test", "Eligibility Reviewer", self.tenant, "password", role=User.Role.REVIEWER)
        household = Household.objects.create(tenant=self.tenant, program=self.program, household_size=1, location="North", registration_date=date.today(), created_by=self.manager)
        beneficiary = Beneficiary.objects.create(household=household, number="ELIG-001", full_name="Eligibility Beneficiary", created_by=self.field_officer)
        enrollment = Enrollment.objects.create(beneficiary=beneficiary, program=self.program, eligibility_status=Enrollment.EligibilityStatus.PENDING, status=Enrollment.Status.ENROLLED)
        self.client.force_authenticate(reviewer)
        response = self.client.patch(f"/api/enrollments/{enrollment.id}/", {"eligibility_status": "ELIGIBLE"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.eligibility_status, Enrollment.EligibilityStatus.ELIGIBLE)

    def test_manager_can_use_field_and_reviewer_workflows(self):
        self.client.force_authenticate(self.manager)
        household_response = self.client.post(
            "/api/households/",
            {"program": str(self.program.id), "household_size": 3, "location": "North", "registration_date": str(date.today())},
            format="json",
        )
        self.assertEqual(household_response.status_code, 201)
        beneficiary_response = self.client.post(
            "/api/beneficiaries/",
            {"household": household_response.data["id"], "national_id": "manager-001", "number": "manager-001", "full_name": "Manager Registered", "gender": "F", "consent_given": True},
            format="json",
        )
        self.assertEqual(beneficiary_response.status_code, 201, beneficiary_response.data)
        enrollment_response = self.client.post(
            "/api/enrollments/",
            {"program": str(self.program.id), "beneficiary": beneficiary_response.data["id"], "eligibility_status": "ELIGIBLE", "status": "ENROLLED"},
            format="json",
        )
        self.assertEqual(enrollment_response.status_code, 201)
        decision_response = self.client.patch(
            f"/api/enrollments/{enrollment_response.data['id']}/",
            {"status": "APPROVED"},
            format="json",
        )
        self.assertEqual(decision_response.status_code, 200)

    def test_field_officer_cannot_request_another_tenant_pdm_summary(self):
        self.client.force_authenticate(self.field_officer)
        response = self.client.get(f"/api/pdm/summary/?tenant={self.other_tenant.id}")
        self.assertEqual(response.status_code, 403)

    def test_manager_can_submit_and_view_only_own_tenant_pdm(self):
        other_manager = User.objects.create_user("other-manager@example.test", "Other Tenant Manager", self.other_tenant, "password", role=User.Role.MANAGER)
        other_program = Program.objects.create(
            tenant=self.other_tenant,
            name="Other cash support",
            country="Kenya",
            country_code="KE",
            currency="KES",
            reporting_currency="KES",
            transfer_amount="50.00",
            payment_cycle=Program.Cycle.MONTHLY,
            created_by=other_manager,
        )
        PDMResponse.objects.create(
            tenant=self.tenant,
            program=self.program,
            channel="MOBILE_MONEY",
            location="North",
            received_rate="90.00",
            amount_received="45.00",
            created_by=self.manager,
        )
        PDMResponse.objects.create(
            tenant=self.other_tenant,
            program=other_program,
            channel="BANK",
            location="South",
            received_rate="80.00",
            amount_received="40.00",
            created_by=other_manager,
        )
        self.client.force_authenticate(self.manager)
        records = self.client.get("/api/pdm/")
        self.assertEqual(records.status_code, 200)
        self.assertEqual(len(records.data), 1)
        self.assertEqual(str(records.data[0]["tenant"]), str(self.tenant.id))
        summary = self.client.get(f"/api/pdm/summary/?program={self.program.id}")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.data["responses"], 1)
        self.assertEqual(summary.data["channels"], ["MOBILE_MONEY"])
        cross_tenant = self.client.get(f"/api/pdm/summary/?program={other_program.id}")
        self.assertIn(cross_tenant.status_code, {403, 404})
        cross_tenant_query = self.client.get(f"/api/pdm/?tenant={self.other_tenant.id}")
        self.assertEqual(cross_tenant_query.status_code, 403)

    def test_platform_admin_can_view_global_and_selected_tenant_pdm(self):
        PDMResponse.objects.create(
            tenant=self.tenant,
            program=self.program,
            channel="MOBILE_MONEY",
            location="North",
            received_rate="90.00",
            amount_received="45.00",
            created_by=self.manager,
        )
        platform_admin = User.objects.create_superuser("platform@example.test", "Platform Administrator", "password")
        self.client.force_authenticate(platform_admin)
        global_summary = self.client.get("/api/pdm/summary/")
        self.assertEqual(global_summary.status_code, 200)
        self.assertEqual(global_summary.data["responses"], 1)
        tenant_summary = self.client.get(f"/api/pdm/summary/?tenant={self.tenant.id}")
        self.assertEqual(tenant_summary.status_code, 200)
        self.assertEqual(tenant_summary.data["tenant"]["id"], str(self.tenant.id))
        program_summary = self.client.get(f"/api/pdm/summary/?tenant={self.tenant.id}&program={self.program.id}")
        self.assertEqual(program_summary.status_code, 200)
        self.assertEqual(program_summary.data["program"]["id"], str(self.program.id))
        mismatched_program = Program.objects.create(
            tenant=platform_admin.tenant,
            name="System program",
            country="Sudan",
            country_code="SD",
            currency="USD",
            reporting_currency="USD",
            transfer_amount="10.00",
            payment_cycle=Program.Cycle.ONE_TIME,
            created_by=platform_admin,
        )
        mismatch = self.client.get(f"/api/pdm/?tenant={self.tenant.id}&program={mismatched_program.id}")
        self.assertEqual(mismatch.status_code, 403)

    def test_import_preview_normalizes_phone_number_header(self):
        self.client.force_authenticate(self.field_officer)
        upload = SimpleUploadedFile(
            "beneficiaries.csv",
            b"client_generated_id,household_size,location,national_id,full_name,Phone Number\nHH-CSV-1,2,North,NID-CSV-1,CSV Beneficiary,+249 91 234 5678\n",
            content_type="text/csv",
        )

        response = self.client.post("/api/imports/", {"program_id": str(self.program.id), "file": upload}, format="multipart")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["rows_valid"], 1)
        self.assertEqual(response.data["preview_rows"][0]["phone_number"], "+249912345678")

    def test_reconciliation_rejects_non_object_provider_report(self):
        self.client.force_authenticate(self.manager)

        response = self.client.post("/api/ai/reconcile/", {"batch_id": "unused", "provider_report_data": []}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("JSON object", response.data["error"]["message"])

    def test_assigned_complaint_is_visible_on_assignee_dashboard(self):
        beneficiary = self.create_successful_payment()
        support = User.objects.create_user("dashboard-support@example.test", "Dashboard Support", self.tenant, "password", role=User.Role.SUPPORT)
        Complaint.objects.create(
            beneficiary=beneficiary,
            category="Access",
            description="Dashboard assignment test.",
            assigned_to=support,
            created_by=self.manager,
        )
        self.client.force_authenticate(support)

        response = self.client.get("/api/reports/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["dashboards"]["assigned_complaints"]), 1)
        self.assertEqual(response.data["dashboards"]["assigned_complaints"][0]["beneficiary__full_name"], beneficiary.full_name)


class EndToEndWorkflowTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Partner A", tenant_type="NGO", default_currency="USD")
        self.manager = User.objects.create_user("manager@example.test", "Tenant Manager", self.tenant, "password", role=User.Role.MANAGER)
        self.field_officer = User.objects.create_user("field@example.test", "Field Officer", self.tenant, "password", role=User.Role.FIELD_OFFICER)
        self.reviewer = User.objects.create_user("reviewer@example.test", "Reviewer", self.tenant, "password", role=User.Role.REVIEWER)
        self.finance = User.objects.create_user("finance@example.test", "Finance Officer", self.tenant, "password", role=User.Role.FINANCE)
        self.support = User.objects.create_user("support@example.test", "Support Officer", self.tenant, "password", role=User.Role.SUPPORT)
        self.auditor = User.objects.create_user("auditor@example.test", "Auditor", self.tenant, "password", role=User.Role.AUDITOR)
        self.client = APIClient()

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def test_end_to_end_workflow_and_role_scoped_reporting(self):
        self.authenticate(self.manager)
        program_response = self.client.post(
            "/api/programs/",
            {
                "name": "Cash assistance",
                "country": "Sudan",
                "country_code": "SD",
                "currency": "USD",
                "currency_type": "PROGRAM",
                "reporting_currency": "USD",
                "exchange_rate": "1",
                "timezone": "Africa/Khartoum",
                "language": "en",
                "transfer_amount": "50.00",
                "payment_cycle": "MONTHLY",
                "status": "ACTIVE",
                "workflow_config": {"payment_enabled": True},
                "country_pack": {},
            },
            format="json",
        )
        self.assertEqual(program_response.status_code, 201)
        program_id = program_response.data["id"]
        budget_response = self.client.post(
            "/api/budgets/",
            {"program": program_id, "currency": "USD", "status": "ACTIVE", "planned_total": "1000.00", "actual_total": "0.00", "line_items": []},
            format="json",
        )
        self.assertEqual(budget_response.status_code, 201)

        self.authenticate(self.finance)
        channel_response = self.client.post(
            f"/api/programs/{program_id}/channels/",
            {"channel_type": "MOBILE_MONEY", "provider_name": "Internal simulator", "currency": "USD", "is_active": True},
            format="json",
        )
        self.assertEqual(channel_response.status_code, 201)
        batch_response = self.client.post(
            "/api/payment-batches/",
            {"program": program_id, "name": "September simulator", "status": "DRAFT", "provider_response": {}},
            format="json",
        )
        self.assertEqual(batch_response.status_code, 201)

        self.authenticate(self.field_officer)
        household_response = self.client.post(
            "/api/households/",
            {"program": program_id, "household_size": 4, "location": "Khartoum", "registration_date": "2026-09-25"},
            format="json",
        )
        self.assertEqual(household_response.status_code, 201)
        beneficiary_response = self.client.post(
            "/api/beneficiaries/",
            {"household": household_response.data["id"], "national_id": "BEN-001", "full_name": "Amina Ahmed", "gender": "F", "phone_number": "+249 91 234 1234", "consent_given": True},
            format="json",
        )
        self.assertEqual(beneficiary_response.status_code, 201)
        beneficiary_id = beneficiary_response.data["id"]

        duplicate_household_response = self.client.post(
            "/api/households/",
            {"program": program_id, "household_size": 3, "location": "Khartoum", "registration_date": "2026-09-25"},
            format="json",
        )
        self.assertEqual(duplicate_household_response.status_code, 201)
        duplicate_beneficiary_response = self.client.post(
            "/api/beneficiaries/",
            {"household": duplicate_household_response.data["id"], "national_id": "BEN-002", "full_name": "Amina Ahmed", "gender": "F", "phone_number": "+249 91 234 1234", "consent_given": True},
            format="json",
        )
        self.assertEqual(duplicate_beneficiary_response.status_code, 201)

        self.authenticate(self.reviewer)
        dedup_response = self.client.post(f"/api/ai/deduplicate/{beneficiary_id}/", {}, format="json")
        self.assertEqual(dedup_response.status_code, 200)
        self.assertTrue(dedup_response.data["duplicate_flagged"])
        signal = AISignal.objects.get(entity_id=beneficiary_id, signal_type="POSSIBLE_DUPLICATE")
        review_response = self.client.post(
            f"/api/ai-signals/{signal.id}/review/",
            {"status": "REVIEWED", "review_note": "Evidence checked; keep records separate pending documents."},
            format="json",
        )
        self.assertEqual(review_response.status_code, 200)
        enrollment_response = self.client.post(
            "/api/enrollments/",
            {"program": program_id, "beneficiary": beneficiary_id, "eligibility_status": "ELIGIBLE", "status": "ENROLLED"},
            format="json",
        )
        self.assertEqual(enrollment_response.status_code, 201)
        approval_response = self.client.patch(
            f"/api/enrollments/{enrollment_response.data['id']}/",
            {"status": "APPROVED"},
            format="json",
        )
        self.assertEqual(approval_response.status_code, 200)
        household = Household.objects.get(pk=household_response.data["id"])
        HouseholdEligibility.objects.create(
            household=household,
            program=Program.objects.get(pk=program_id),
            status=HouseholdEligibility.Status.ELIGIBLE,
            decided_by=self.reviewer,
        )
        HouseholdEnrollment.objects.create(
            household=household,
            program=Program.objects.get(pk=program_id),
            status=HouseholdEnrollment.Status.APPROVED,
            assistance_modality=Enrollment.AssistanceModality.CASH,
            decided_by=self.reviewer,
        )

        self.authenticate(self.finance)
        cash_entitlement_response = self.client.post(
            "/api/cash-entitlements/",
            {"household": str(household.id), "beneficiary": beneficiary_id, "program": program_id, "amount": "50.00", "currency": "USD"},
            format="json",
        )
        self.assertEqual(cash_entitlement_response.status_code, 201, cash_entitlement_response.data)
        instruction_response = self.client.post(
            "/api/payment-instructions/",
            {
                "batch": batch_response.data["id"],
                "cash_entitlement": cash_entitlement_response.data["id"],
                "channel_config": channel_response.data["id"],
                "amount": "50.00",
                "currency": "USD",
            },
            format="json",
        )
        self.assertEqual(instruction_response.status_code, 201)
        instruction_id = instruction_response.data["id"]
        simulation_response = self.client.post(
            f"/api/payment-instructions/{instruction_id}/simulate/",
            {"outcome": "success"},
            format="json",
        )
        self.assertEqual(simulation_response.status_code, 200)
        instruction = PaymentInstruction.objects.get(id=instruction_id)
        anomaly_response = self.client.post("/api/ai/anomalies/", {"batch_id": batch_response.data["id"]}, format="json")
        self.assertEqual(anomaly_response.status_code, 200)
        reconciliation_response = self.client.post(
            "/api/ai/reconcile/",
            {"batch_id": batch_response.data["id"], "provider_report_data": {instruction.provider_reference: {"amount": "50.00", "status": "SUCCESS"}}},
            format="json",
        )
        self.assertEqual(reconciliation_response.status_code, 200)

        self.authenticate(self.field_officer)
        pdm_response = self.client.post(
            "/api/pdm/",
            {"program": program_id, "received_count": 1, "access_problem_rate": 0, "satisfaction": 100},
            format="json",
        )
        self.assertEqual(pdm_response.status_code, 201)

        self.authenticate(self.support)
        complaint_response = self.client.post(
            "/api/complaints/",
            {"beneficiary": beneficiary_id, "category": "Information request", "description": "Beneficiary asked about the payment date.", "severity": "LOW", "status": "OPEN"},
            format="json",
        )
        self.assertEqual(complaint_response.status_code, 201)

        self.authenticate(self.manager)
        assign_response = self.client.patch(
            f"/api/complaints/{complaint_response.data['id']}/",
            {"assigned_to": str(self.support.id), "status": "IN_PROGRESS", "resolution_notes": "Assigned for follow-up."},
            format="json",
        )
        self.assertEqual(assign_response.status_code, 200)

        self.authenticate(self.support)
        resolve_response = self.client.patch(
            f"/api/complaints/{complaint_response.data['id']}/",
            {"status": "RESOLVED", "resolution_notes": "Payment date explained to the beneficiary."},
            format="json",
        )
        self.assertEqual(resolve_response.status_code, 200)

        self.authenticate(self.manager)
        manager_dashboard = self.client.get(f"/api/reports/?program={program_id}")
        self.assertEqual(manager_dashboard.status_code, 200)
        self.assertEqual(manager_dashboard.data["dashboards"]["paid"], 1)
        self.assertEqual(manager_dashboard.data["dashboards"]["budget_planned"], "1000.00")
        self.assertEqual(self.client.get("/api/audit-events/").status_code, 200)

        self.authenticate(self.field_officer)
        field_dashboard = self.client.get(f"/api/reports/?program={program_id}")
        self.assertEqual(field_dashboard.status_code, 200)
        self.assertEqual(set(field_dashboard.data["dashboards"]), {"beneficiaries", "approvals"})

        self.authenticate(self.auditor)
        self.assertEqual(self.client.get("/api/audit-events/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/reporting/programs/{program_id}/summary/").status_code, 200)

    def test_ineligible_enrollment_cannot_be_approved(self):
        program = Program.objects.create(
            tenant=self.tenant,
            name="Cash assistance",
            country="Sudan",
            country_code="SD",
            currency="USD",
            reporting_currency="USD",
            transfer_amount="50.00",
            payment_cycle=Program.Cycle.MONTHLY,
            created_by=self.manager,
        )
        self.authenticate(self.field_officer)
        household_response = self.client.post(
            "/api/households/",
            {"program": str(program.id), "household_size": 2, "location": "Omdurman", "registration_date": "2026-09-25"},
            format="json",
        )
        beneficiary_response = self.client.post(
            "/api/beneficiaries/",
            {"household": household_response.data["id"], "number": "BEN-003", "full_name": "Mahmoud Ali", "gender": "M", "consent_given": True},
            format="json",
        )
        self.authenticate(self.reviewer)
        enrollment_response = self.client.post(
            "/api/enrollments/",
            {"program": str(program.id), "beneficiary": beneficiary_response.data["id"], "eligibility_status": "INELIGIBLE", "status": "ENROLLED"},
            format="json",
        )
        self.assertEqual(enrollment_response.status_code, 201)
        response = self.client.patch(
            f"/api/enrollments/{enrollment_response.data['id']}/",
            {"status": "APPROVED"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class DeletionAccessTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", tenant_type="NGO", default_currency="USD")
        self.other_tenant = Tenant.objects.create(name="Tenant B", tenant_type="NGO", default_currency="USD")
        self.manager = User.objects.create_user("manager-delete@example.test", "Tenant Manager", self.tenant, "password", role=User.Role.MANAGER)
        self.field_officer = User.objects.create_user("field-delete@example.test", "Field Officer", self.tenant, "password", role=User.Role.FIELD_OFFICER)
        self.other_manager = User.objects.create_user("other-manager-delete@example.test", "Other Manager", self.other_tenant, "password", role=User.Role.MANAGER)
        self.program = self.make_program(self.tenant, self.manager, "Tenant A program")
        self.other_program = self.make_program(self.other_tenant, self.other_manager, "Tenant B program")
        self.client = APIClient()

    def make_program(self, tenant, creator, name):
        return Program.objects.create(
            tenant=tenant,
            name=name,
            country="Sudan",
            country_code="SD",
            currency="USD",
            reporting_currency="USD",
            transfer_amount="50.00",
            payment_cycle=Program.Cycle.MONTHLY,
            created_by=creator,
        )

    def make_beneficiary_graph(self, program, creator):
        household = Household.objects.create(
            tenant=program.tenant,
            program=program,
            household_size=2,
            location="Khartoum",
            registration_date=date.today(),
            created_by=creator,
        )
        beneficiary = Beneficiary.objects.create(
            household=household,
            number="DELETE-001",
            full_name="Delete Test Beneficiary",
            created_by=creator,
        )
        enrollment = Enrollment.objects.create(
            beneficiary=beneficiary,
            program=program,
            eligibility_status=Enrollment.EligibilityStatus.ELIGIBLE,
            status=Enrollment.Status.APPROVED,
            approved_by=creator,
        )
        channel = PaymentChannelConfig.objects.create(
            program=program,
            channel_type=PaymentChannelConfig.ChannelType.MOBILE_MONEY,
            provider_name="Simulator",
            currency="USD",
        )
        instruction = PaymentInstruction.objects.create(
            enrollment=enrollment,
            beneficiary=beneficiary,
            channel_config=channel,
            amount="50.00",
            currency="USD",
            status=PaymentInstruction.Status.SUCCESS,
            idempotency_key=f"delete-{beneficiary.id}",
            created_by=creator,
        )
        PaymentEvent.objects.create(
            instruction=instruction,
            event_type=PaymentEvent.EventType.SUCCESS,
            provider_status=PaymentEvent.ProviderStatus.SETTLED,
            to_status=PaymentInstruction.Status.SUCCESS,
            recorded_by=creator,
        )
        Complaint.objects.create(
            beneficiary=beneficiary,
            instruction=instruction,
            category="Access",
            description="Deletion test complaint",
            created_by=creator,
        )
        return household, beneficiary, enrollment, instruction

    def test_manager_can_delete_program_and_its_protected_dependencies(self):
        household, beneficiary, enrollment, instruction = self.make_beneficiary_graph(self.program, self.manager)
        Budget.objects.create(program=self.program, currency="USD", planned_total="100.00")
        self.client.force_authenticate(self.manager)

        response = self.client.delete(f"/api/programs/{self.program.id}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Program.objects.filter(id=self.program.id).exists())
        self.assertFalse(Household.objects.filter(id=household.id).exists())
        self.assertFalse(Beneficiary.objects.filter(id=beneficiary.id).exists())
        self.assertFalse(Enrollment.objects.filter(id=enrollment.id).exists())
        self.assertFalse(PaymentInstruction.objects.filter(id=instruction.id).exists())
        self.assertTrue(AuditEvent.objects.filter(action="PROGRAM_DELETED", entity_id=str(self.program.id)).exists())

    def test_manager_can_delete_beneficiary_and_payment_dependencies(self):
        household, beneficiary, enrollment, instruction = self.make_beneficiary_graph(self.program, self.manager)
        self.client.force_authenticate(self.manager)

        response = self.client.delete(f"/api/beneficiaries/{beneficiary.id}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Household.objects.filter(id=household.id).exists())
        self.assertFalse(Beneficiary.objects.filter(id=beneficiary.id).exists())
        self.assertFalse(Enrollment.objects.filter(id=enrollment.id).exists())
        self.assertFalse(PaymentInstruction.objects.filter(id=instruction.id).exists())
        self.assertTrue(AuditEvent.objects.filter(action="BENEFICIARY_DELETED").exists())
        self.assertTrue(AuditEvent.objects.filter(action="HOUSEHOLD_DELETED_AFTER_LAST_BENEFICIARY").exists())

    def test_field_officer_cannot_delete_beneficiary(self):
        _, beneficiary, _, _ = self.make_beneficiary_graph(self.program, self.manager)
        self.client.force_authenticate(self.field_officer)

        response = self.client.delete(f"/api/beneficiaries/{beneficiary.id}/")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Beneficiary.objects.filter(id=beneficiary.id).exists())

    def test_manager_cannot_delete_another_tenant_program(self):
        self.client.force_authenticate(self.manager)

        response = self.client.delete(f"/api/programs/{self.other_program.id}/")

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Program.objects.filter(id=self.other_program.id).exists())


class ProgramConditionalModalityTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Conditional Tenant", tenant_type="NGO", default_currency="USD")
        self.manager = User.objects.create_user("conditional-manager@example.test", "Conditional Manager", self.tenant, "password", role=User.Role.MANAGER)
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def payload(self, **overrides):
        payload = {"name": "Conditional response", "country": "Sudan", "country_code": "SD", "currency": "USD", "currency_type": "PROGRAM", "reporting_currency": "USD", "exchange_rate": "1", "status": "DRAFT", "cash_enabled": False, "nfi_enabled": True, "other_assistance_enabled": False, "workflow_config": {}, "country_pack": {}}
        payload.update(overrides)
        return payload

    def test_nfi_only_program_does_not_require_cash_fields(self):
        response = self.client.post("/api/programs/", self.payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        program = Program.objects.get(id=response.data["id"])
        self.assertIsNone(program.transfer_amount)
        self.assertEqual(program.payment_cycle, "")

    def test_cash_enabled_program_requires_valid_cash_fields(self):
        missing = self.client.post("/api/programs/", self.payload(name="Missing cash", cash_enabled=True), format="json")
        self.assertEqual(missing.status_code, 400)
        self.assertIn("transfer_amount", missing.data["error"]["fields"])
        below_minimum = self.client.post("/api/programs/", self.payload(name="Small cash", cash_enabled=True, transfer_amount="0", payment_cycle="ONE_TIME"), format="json")
        self.assertEqual(below_minimum.status_code, 400)
        valid = self.client.post("/api/programs/", self.payload(name="Cash and NFI", cash_enabled=True, transfer_amount="100.00", payment_cycle="ONE_TIME"), format="json")
        self.assertEqual(valid.status_code, 201, valid.data)

    def test_switching_cash_off_clears_cash_fields(self):
        program = Program.objects.create(tenant=self.tenant, name="Existing cash", country="Sudan", currency="USD", transfer_amount="50.00", payment_cycle=Program.Cycle.MONTHLY, cash_enabled=True, created_by=self.manager)
        response = self.client.patch(f"/api/programs/{program.id}/", {"cash_enabled": False, "nfi_enabled": True, "transfer_amount": None, "payment_cycle": ""}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        program.refresh_from_db()
        self.assertIsNone(program.transfer_amount)
        self.assertEqual(program.payment_cycle, "")

    def test_repeated_idempotency_key_replays_the_program_response(self):
        payload = self.payload(name="Idempotent NFI response")
        headers = {"HTTP_IDEMPOTENCY_KEY": "program-nfi-response-1"}
        first = self.client.post("/api/programs/", payload, format="json", **headers)
        second = self.client.post("/api/programs/", payload, format="json", **headers)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(Program.objects.filter(name="Idempotent NFI response").count(), 1)


class SharedApiRegressionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Shared API Tenant", tenant_type="NGO", default_currency="USD")
        self.manager = User.objects.create_user("shared-api@example.test", "Shared API Manager", self.tenant, "password", role=User.Role.MANAGER)
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def program_payload(self, name="Current program"):
        return {
            "name": name,
            "country": "Sudan",
            "country_code": "SD",
            "currency": "USD",
            "currency_type": "PROGRAM",
            "reporting_currency": "USD",
            "exchange_rate": "1",
            "status": "DRAFT",
            "cash_enabled": False,
            "nfi_enabled": True,
            "workflow_config": {},
            "country_pack": {},
        }

    def test_json_safe_recursively_converts_uuid_datetime_decimal(self):
        value = json_safe({"id": uuid.uuid4(), "when": datetime.now(timezone.utc), "amount": Decimal("1.25"), "rows": [uuid.uuid4()]})
        self.assertIsInstance(value["id"], str)
        self.assertIsInstance(value["when"], str)
        self.assertEqual(value["amount"], "1.25")
        self.assertIsInstance(value["rows"][0], str)
        json.dumps(value, cls=DjangoJSONEncoder)

    def test_post_get_patch_returns_current_uuid_safe_records(self):
        created = self.client.post("/api/programs/", self.program_payload(), format="json")
        self.assertEqual(created.status_code, 201, created.data)
        program_id = created.data["id"]
        self.assertIsInstance(program_id, str)

        listed = self.client.get("/api/programs/")
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed["Cache-Control"], "no-store, no-cache, must-revalidate, max-age=0")
        self.assertTrue(any(row["id"] == program_id for row in listed.data["results"]))

        updated = self.client.patch(f"/api/programs/{program_id}/", {"name": "Renamed program"}, format="json")
        self.assertEqual(updated.status_code, 200, updated.data)
        current = self.client.get(f"/api/programs/{program_id}/")
        self.assertEqual(current.status_code, 200, current.data)
        self.assertEqual(current.data["name"], "Renamed program")


class NFIWorkflowTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="NFI Tenant", tenant_type="NGO", default_currency="USD")
        self.manager = User.objects.create_user("nfi-manager@example.test", "NFI Manager", self.tenant, "password", role=User.Role.MANAGER)
        self.finance = User.objects.create_user("nfi-finance@example.test", "NFI Finance", self.tenant, "password", role=User.Role.FINANCE)
        self.reviewer = User.objects.create_user("nfi-reviewer@example.test", "NFI Reviewer", self.tenant, "password", role=User.Role.REVIEWER)
        self.field_officer = User.objects.create_user("nfi-field@example.test", "NFI Field Officer", self.tenant, "password", role=User.Role.FIELD_OFFICER)
        self.program = Program.objects.create(name="NFI response", tenant=self.tenant, country="Sudan", currency="USD", transfer_amount="1.00", payment_cycle=Program.Cycle.ONE_TIME, cash_enabled=False, nfi_enabled=True, created_by=self.manager)
        self.household = Household.objects.create(tenant=self.tenant, program=self.program, household_size=3, location="Khartoum", registration_date=date.today(), created_by=self.manager)
        self.beneficiary = Beneficiary.objects.create(household=self.household, number="NFI-001", full_name="NFI Beneficiary", created_by=self.manager)
        self.enrollment = Enrollment.objects.create(beneficiary=self.beneficiary, program=self.program, eligibility_status=Enrollment.EligibilityStatus.ELIGIBLE, status=Enrollment.Status.APPROVED, assistance_modality=Enrollment.AssistanceModality.NFI, approved_by=self.reviewer)
        self.household_enrollment = HouseholdEnrollment.objects.create(
            household=self.household,
            program=self.program,
            status=HouseholdEnrollment.Status.APPROVED,
            assistance_modality=Enrollment.AssistanceModality.NFI,
            decided_by=self.reviewer,
        )
        self.warehouse = Warehouse.objects.create(program=self.program, name="Main warehouse", location="Khartoum", person_in_charge="Storekeeper", created_by=self.manager)
        self.item = NFIItem.objects.create(program=self.program, warehouse=self.warehouse, name="Food Kit", item_type="Food", unit="kit", initial_quantity=100, available_quantity=100, created_by=self.manager)
        self.client = APIClient()

    def test_nfi_items_list_includes_committed_and_delivered_quantities(self):
        self.client.force_authenticate(self.manager)

        response = self.client.get("/api/nfi-items/")

        self.assertEqual(response.status_code, 200, response.data)
        item_data = next(row for row in response.data["results"] if row["id"] == str(self.item.id))
        self.assertEqual(item_data["committed_quantity"], 0)
        self.assertEqual(item_data["delivered_quantity"], 0)

    def test_approved_nfi_and_cash_nfi_households_are_available_for_entitlement(self):
        self.client.force_authenticate(self.finance)
        for modality in (
            Enrollment.AssistanceModality.NFI,
            Enrollment.AssistanceModality.CASH_NFI,
        ):
            self.household_enrollment.assistance_modality = modality
            self.household_enrollment.save(update_fields=["assistance_modality"])

            enrollments = self.client.get("/api/household-enrollments/")
            self.assertEqual(enrollments.status_code, 200, enrollments.data)
            household_rows = [
                row
                for row in enrollments.data["results"]
                if str(row["household"]) == str(self.household.id)
            ]
            self.assertTrue(household_rows, enrollments.data)
            household_enrollment = household_rows[0]
            self.assertEqual(str(household_enrollment["program"]), str(self.program.id))
            self.assertEqual(household_enrollment["household_size"], 3)
            self.assertEqual(household_enrollment["member_count"], 1)

            response = self.client.post(
                "/api/nfi-entitlements/",
                {
                    "household": str(self.household.id),
                    "program": str(self.program.id),
                    "warehouse": str(self.warehouse.id),
                    "item": str(self.item.id),
                    "quantity": 1,
                },
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(str(response.data["household"]), str(self.household.id))
            self.assertEqual(response.data["household_members"][0]["full_name"], self.beneficiary.full_name)

    def test_beneficiaries_cannot_exceed_household_size_or_lower_size_below_members(self):
        self.client.force_authenticate(self.manager)
        for national_id in ("NFI-002", "NFI-003"):
            response = self.client.post(
                "/api/beneficiaries/",
                {
                    "household": str(self.household.id),
                    "national_id": national_id,
                    "full_name": f"Member {national_id}",
                },
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.data)

        excess_member = self.client.post(
            "/api/beneficiaries/",
            {
                "household": str(self.household.id),
                "national_id": "NFI-004",
                "full_name": "Over-limit member",
            },
            format="json",
        )
        reduce_size = self.client.patch(
            f"/api/households/{self.household.id}/",
            {"household_size": 2},
            format="json",
        )

        self.assertEqual(excess_member.status_code, 400, excess_member.data)
        self.assertIn("maximum 3", str(excess_member.data))
        self.assertEqual(reduce_size.status_code, 400, reduce_size.data)
        self.assertIn("3 beneficiaries", str(reduce_size.data))

    def test_nfi_entitlement_cannot_exceed_available_stock(self):
        self.client.force_authenticate(self.finance)

        response = self.client.post(
            "/api/nfi-entitlements/",
            {
                "household": str(self.household.id),
                "program": str(self.program.id),
                "warehouse": str(self.warehouse.id),
                "item": str(self.item.id),
                "quantity": 101,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Available: 100", str(response.data))
        self.item.refresh_from_db()
        self.assertEqual(self.item.available_quantity, 100)

    def test_entitlement_edit_excludes_its_existing_reservation_from_available_stock(self):
        self.client.force_authenticate(self.finance)
        created = self.client.post(
            "/api/nfi-entitlements/",
            {
                "household": str(self.household.id),
                "program": str(self.program.id),
                "warehouse": str(self.warehouse.id),
                "item": str(self.item.id),
                "quantity": 3,
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)

        update = self.client.patch(
            f"/api/nfi-entitlements/{created.data['id']}/",
            {"quantity": 100},
            format="json",
        )
        over_update = self.client.patch(
            f"/api/nfi-entitlements/{created.data['id']}/",
            {"quantity": 101},
            format="json",
        )

        self.assertEqual(update.status_code, 200, update.data)
        self.assertEqual(update.data["quantity"], 100)
        self.assertEqual(over_update.status_code, 400, over_update.data)
        self.item.refresh_from_db()
        self.assertEqual(self.item.available_quantity, 0)

    def test_distribution_event_cannot_reserve_more_than_warehouse_stock(self):
        self.item.initial_quantity = 2
        self.item.available_quantity = 2
        self.item.save(update_fields=["initial_quantity", "available_quantity"])
        entitlement = NFIEntitlement.objects.create(
            household=self.household,
            program=self.program,
            item=self.item,
            warehouse=self.warehouse,
            quantity=3,
            created_by=self.finance,
        )
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            "/api/distribution-events/",
            {
                "program": str(self.program.id),
                "warehouse": str(self.warehouse.id),
                "event_date": str(date.today()),
                "location": "Distribution point",
                "distribution_team": "Team A",
                "status": "PLANNED",
                "entitlement_ids": [str(entitlement.id)],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Available: 2", str(response.data))
        self.assertFalse(DistributionAllocation.objects.filter(entitlement=entitlement).exists())

    def test_nfi_entitlement_rejects_household_from_another_program(self):
        other_program = Program.objects.create(
            name="Other NFI response",
            tenant=self.tenant,
            country="Sudan",
            currency="USD",
            transfer_amount="1.00",
            payment_cycle=Program.Cycle.ONE_TIME,
            cash_enabled=False,
            nfi_enabled=True,
            created_by=self.manager,
        )
        self.client.force_authenticate(self.finance)

        response = self.client.post(
            "/api/nfi-entitlements/",
            {
                "household": str(self.household.id),
                "program": str(other_program.id),
                "warehouse": str(self.warehouse.id),
                "item": str(self.item.id),
                "quantity": 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Household must be registered", str(response.data))

    def test_finance_reserves_once_event_uses_entitlement_and_reviewer_records_delivery(self):
        self.client.force_authenticate(self.finance)
        entitlement_response = self.client.post("/api/nfi-entitlements/", {"household": str(self.household.id), "beneficiary": str(self.beneficiary.id), "program": str(self.program.id), "warehouse": str(self.warehouse.id), "item": str(self.item.id), "quantity": 3}, format="json")
        self.assertEqual(entitlement_response.status_code, 201, entitlement_response.data)
        self.assertEqual(entitlement_response.data["program_name"], self.program.name)
        self.assertEqual(entitlement_response.data["beneficiary_name"], self.beneficiary.full_name)
        self.assertEqual(entitlement_response.data["national_id_reference"], "•••• " + self.beneficiary.number[-4:])
        self.assertNotEqual(entitlement_response.data["national_id_reference"], str(self.beneficiary.id))
        self.assertEqual(entitlement_response.data["item_name"], self.item.name)
        self.assertEqual(entitlement_response.data["warehouse_name"], self.warehouse.name)
        self.assertEqual(entitlement_response.data["household_reference"], self.household.registration_reference or f"HH-{str(self.household.id).split('-')[0].upper()}")
        entitlement = NFIEntitlement.objects.get(id=entitlement_response.data["id"])
        self.item.refresh_from_db()
        self.assertEqual(self.item.available_quantity, 97)

        self.client.force_authenticate(self.manager)
        event_response = self.client.post("/api/distribution-events/", {"program": str(self.program.id), "warehouse": str(self.warehouse.id), "event_date": str(date.today()), "location": "Distribution point", "distribution_team": "Team A", "status": "PLANNED", "entitlement_ids": [str(entitlement.id)]}, format="json")
        self.assertEqual(event_response.status_code, 201, event_response.data)
        self.assertEqual(event_response.data["program_name"], self.program.name)
        self.assertEqual(event_response.data["warehouse_name"], self.warehouse.name)
        allocation = DistributionAllocation.objects.get(event_id=event_response.data["id"], entitlement=entitlement)
        self.assertEqual(allocation.planned_quantity, 3)
        allocation_response = self.client.get("/api/distribution-allocations/")
        self.assertEqual(allocation_response.status_code, 200, allocation_response.data)
        allocation_data = allocation_response.data["results"][0]
        self.assertEqual(allocation_data["beneficiary_name"], self.beneficiary.full_name)
        self.assertEqual(allocation_data["national_id_reference"], "•••• " + self.beneficiary.number[-4:])
        self.assertEqual(allocation_data["item_name"], self.item.name)
        self.item.refresh_from_db()
        self.assertEqual(self.item.available_quantity, 97)

        self.client.force_authenticate(self.reviewer)
        issue_response = self.client.post("/api/distribution-issues/", {"beneficiary": str(self.beneficiary.id), "entitlement": str(entitlement.id), "item": str(self.item.id), "event": event_response.data["id"], "planned_quantity": 3, "actual_quantity": 3, "delivery_status": "RECEIVED"}, format="json")
        self.assertEqual(issue_response.status_code, 201, issue_response.data)
        self.assertEqual(issue_response.data["beneficiary_name"], self.beneficiary.full_name)
        self.assertEqual(issue_response.data["national_id_reference"], "•••• " + self.beneficiary.number[-4:])
        self.assertEqual(issue_response.data["item_name"], self.item.name)
        entitlement.refresh_from_db()
        self.assertEqual(entitlement.status, NFIEntitlement.Status.DISTRIBUTED)
        self.assertTrue(AuditEvent.objects.filter(action="DISTRIBUTION_DELIVERY_RECORDED", entity_id=issue_response.data["id"]).exists())

    def test_payment_instruction_is_rejected_for_nfi_only_program(self):
        channel = PaymentChannelConfig.objects.create(program=self.program, channel_type=PaymentChannelConfig.ChannelType.CASH, provider_name="Simulator", currency="USD")
        self.client.force_authenticate(self.finance)
        response = self.client.post("/api/payment-instructions/", {"enrollment": str(self.enrollment.id), "beneficiary": str(self.beneficiary.id), "channel_config": str(channel.id), "amount": "10.00", "currency": "USD"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_separate_allocation_and_delivery_creation_paths_respect_reserved_quantities(self):
        entitlement = NFIEntitlement.objects.create(
            household=self.household,
            beneficiary=self.beneficiary,
            program=self.program,
            item=self.item,
            warehouse=self.warehouse,
            quantity=3,
            created_by=self.finance,
        )
        event = DistributionEvent.objects.create(
            program=self.program,
            warehouse=self.warehouse,
            location="Distribution point",
            event_date=date.today(),
            distribution_team="Team A",
            created_by=self.manager,
        )
        self.client.force_authenticate(self.manager)

        allocation_response = self.client.post(
            "/api/distribution-allocations/",
            {"household": str(self.household.id), "beneficiary": str(self.beneficiary.id), "entitlement": str(entitlement.id), "event": str(event.id), "item": str(self.item.id), "planned_quantity": 3, "allocated_quantity": 3, "status": "ALLOCATED"},
            format="json",
        )
        duplicate_allocation = self.client.post(
            "/api/distribution-allocations/",
            {"household": str(self.household.id), "beneficiary": str(self.beneficiary.id), "entitlement": str(entitlement.id), "event": str(event.id), "item": str(self.item.id), "planned_quantity": 1, "allocated_quantity": 1, "status": "ALLOCATED"},
            format="json",
        )
        self.assertEqual(allocation_response.status_code, 201, allocation_response.data)
        self.assertEqual(duplicate_allocation.status_code, 400, duplicate_allocation.data)

        self.client.force_authenticate(self.reviewer)
        delivery = self.client.post(
            "/api/distribution-issues/",
            {"household": str(self.household.id), "beneficiary": str(self.beneficiary.id), "entitlement": str(entitlement.id), "event": str(event.id), "item": str(self.item.id), "planned_quantity": 3, "actual_quantity": 2, "delivery_status": "PARTIALLY_RECEIVED"},
            format="json",
        )
        over_delivery = self.client.post(
            "/api/distribution-issues/",
            {"household": str(self.household.id), "beneficiary": str(self.beneficiary.id), "entitlement": str(entitlement.id), "event": str(event.id), "item": str(self.item.id), "planned_quantity": 3, "actual_quantity": 2, "delivery_status": "PARTIALLY_RECEIVED"},
            format="json",
        )
        corrected_delivery = self.client.patch(
            f"/api/distribution-issues/{delivery.data['id']}/",
            {"actual_quantity": 3, "delivery_status": "RECEIVED"},
            format="json",
        )

        self.assertEqual(delivery.status_code, 201, delivery.data)
        self.assertEqual(over_delivery.status_code, 400, over_delivery.data)
        self.assertEqual(corrected_delivery.status_code, 200, corrected_delivery.data)
        self.assertEqual(DistributionIssue.objects.filter(entitlement=entitlement).count(), 1)
        entitlement.refresh_from_db()
        self.assertEqual(entitlement.status, NFIEntitlement.Status.DISTRIBUTED)

    def test_reviewer_and_field_officer_cannot_create_nfi_entitlements(self):
        payload = {"beneficiary": str(self.beneficiary.id), "program": str(self.program.id), "warehouse": str(self.warehouse.id), "item": str(self.item.id), "quantity": 1}
        for user in (self.reviewer, self.field_officer):
            self.client.force_authenticate(user)
            response = self.client.post("/api/nfi-entitlements/", payload, format="json")
            self.assertEqual(response.status_code, 403, response.data)
