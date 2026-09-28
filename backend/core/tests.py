from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from datetime import date

from .models import (
    AISignal,
    AuditEvent,
    AutomationRule,
    Beneficiary,
    Complaint,
    Enrollment,
    Household,
    PDMResponse,
    PaymentChannelConfig,
    PaymentEvent,
    PaymentInstruction,
    Budget,
    Program,
    ProgramActivity,
    ReviewTask,
    Tenant,
    User,
)


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

        self.authenticate(self.finance)
        instruction_response = self.client.post(
            "/api/payment-instructions/",
            {
                "batch": batch_response.data["id"],
                "enrollment": enrollment_response.data["id"],
                "beneficiary": beneficiary_id,
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
