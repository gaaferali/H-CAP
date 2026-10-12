from pathlib import Path
from tempfile import NamedTemporaryFile
import sys
from datetime import date

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import SyncOperation, Tenant, User
from assistance.models import Beneficiary, Household
from automation import ai_source
from automation.models import AutomationRule
from programs.models import Program


class AISourceIntegrationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="AI source tenant",
            tenant_type="NGO",
            default_currency="SDG",
        )
        self.user = User.objects.create_user(
            email="ai-source@example.test",
            full_name="AI Source Manager",
            tenant=self.tenant,
            password="password",
            role=User.Role.MANAGER,
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            name="AI source program",
            country="Sudan",
            currency="SDG",
            cash_enabled=True,
            nfi_enabled=False,
            created_by=self.user,
        )

    def test_algorithms_are_loaded_only_from_the_external_source_folder(self):
        source_root = ai_source.SOURCE_ROOT.resolve()
        loaded_files = (
            Path(ai_source._services.__file__).resolve(),
            Path(ai_source._complaints.__file__).resolve(),
            Path(ai_source._duplicates.__file__).resolve(),
        )
        self.assertTrue(all(source_root in path.parents for path in loaded_files))
        command = ai_source.import_data_command_class()
        self.assertTrue(command.__module__.startswith("_hcap_ai_automation_source."))
        self.assertNotIn("core", sys.modules)

    def test_offline_event_uses_the_restored_database_model_idempotently(self):
        first = ai_source.record_offline_event(
            tenant=self.tenant,
            operation_id="offline-001",
            operation_type="REGISTRATION",
            payload={"beneficiary": "A"},
        )
        repeated = ai_source.record_offline_event(
            tenant=self.tenant,
            operation_id="offline-001",
            operation_type="REGISTRATION",
            payload={"beneficiary": "A"},
        )
        self.assertEqual(first["status"], "applied")
        self.assertEqual(repeated["status"], "duplicate")
        self.assertEqual(SyncOperation.objects.count(), 1)

    def test_source_import_command_maps_legacy_database_field_names(self):
        csv_text = (
            "row_id,household_id,size,site,consent,status,full_name\n"
            "ROW-001,SRC-HH-001,4,A,YES,APPROVED,Amina Ahmed\n"
        )
        with NamedTemporaryFile("w", suffix=".csv", encoding="utf-8", delete=False) as csv_file:
            csv_file.write(csv_text)
            csv_path = Path(csv_file.name)
        try:
            call_command(
                "import_data",
                str(csv_path),
                tenant_id=str(self.tenant.id),
                program_id=str(self.program.id),
                user_email=self.user.email,
                verbosity=0,
            )
        finally:
            csv_path.unlink(missing_ok=True)

        household = Household.objects.get(registration_reference="SRC-HH-001")
        self.assertEqual(household.program, self.program)
        self.assertTrue(Beneficiary.objects.filter(household=household, full_name="Amina Ahmed").exists())

    def test_frontend_ai_contract_returns_readable_labels(self):
        household = Household.objects.create(
            tenant=self.tenant,
            program=self.program,
            household_size=2,
            location="Khartoum",
            registration_date=date.today(),
            registration_reference="AI-HH-001",
            created_by=self.user,
        )
        first = Beneficiary.objects.create(
            household=household,
            number="AI-001",
            full_name="Amina Ahmed",
            consent_given=True,
            created_by=self.user,
        )
        Beneficiary.objects.create(
            household=household,
            number="AI-002",
            full_name="Amina Ahmed",
            consent_given=True,
            created_by=self.user,
        )
        AutomationRule.objects.create(
            tenant=self.tenant,
            program=self.program,
            event_name="DUPLICATE_FLAG",
            action_name="CREATE_REVIEW_TASK",
            is_active=True,
        )

        client = APIClient()
        client.force_authenticate(self.user)
        duplicate = client.post(f"/api/ai/deduplicate/{first.id}/", {}, format="json")
        self.assertEqual(duplicate.status_code, 200, duplicate.data)
        self.assertTrue(duplicate.data["duplicate_flagged"])
        self.assertIn("Amina Ahmed", duplicate.data["signals"][0]["entity_label"])

        tasks = client.get("/api/review-tasks/")
        self.assertEqual(tasks.status_code, 200, tasks.data)
        task_rows = tasks.data.get("results", tasks.data)
        self.assertIn("Amina Ahmed", task_rows[0]["entity_label"])

        status = client.get("/api/ai/status/")
        self.assertEqual(status.status_code, 200, status.data)
        self.assertEqual(status.data["rules"][0]["program_name"], self.program.name)
