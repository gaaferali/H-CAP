from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from .models import Beneficiary, Enrollment, Household, NFIItem, Program, Tenant, User, Warehouse


class ModalityMatrixTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Modality Tenant", tenant_type="NGO", default_currency="USD")
        self.manager = User.objects.create_user(
            "modality-manager@example.test", "Modality Manager", self.tenant, "password", role=User.Role.MANAGER
        )
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def make_program(self, name, cash_enabled, nfi_enabled):
        return Program.objects.create(
            tenant=self.tenant,
            name=name,
            country="Sudan",
            country_code="SD",
            currency="USD",
            reporting_currency="USD",
            transfer_amount="50.00" if cash_enabled else None,
            payment_cycle=Program.Cycle.MONTHLY if cash_enabled else "",
            cash_enabled=cash_enabled,
            nfi_enabled=nfi_enabled,
            created_by=self.manager,
        )

    def make_beneficiary(self, program, number):
        household = Household.objects.create(
            tenant=self.tenant,
            program=program,
            household_size=1,
            location="Khartoum",
            registration_date=date.today(),
            created_by=self.manager,
        )
        return Beneficiary.objects.create(
            household=household,
            number=number,
            full_name=f"Beneficiary {number}",
            created_by=self.manager,
        )

    def enroll(self, program, beneficiary, modality=None):
        payload = {
            "program": str(program.id),
            "beneficiary": str(beneficiary.id),
            "eligibility_status": "ELIGIBLE",
            "status": "ENROLLED",
        }
        if modality:
            payload["assistance_modality"] = modality
        return self.client.post("/api/enrollments/", payload, format="json")

    def test_payment_only_allows_cash_and_blocks_nfi(self):
        program = self.make_program("Payment only", True, False)
        beneficiary = self.make_beneficiary(program, "PAY-001")
        cash = self.enroll(program, beneficiary, "CASH")
        self.assertEqual(cash.status_code, 201, cash.data)
        nfi = self.enroll(program, self.make_beneficiary(program, "PAY-002"), "NFI")
        self.assertEqual(nfi.status_code, 400)
        self.assertIn("NFI is disabled", str(nfi.data))

    def test_nfi_only_infers_nfi_and_allows_entitlement(self):
        program = self.make_program("NFI only", False, True)
        beneficiary = self.make_beneficiary(program, "NFI-001")
        enrollment = self.enroll(program, beneficiary)
        self.assertEqual(enrollment.status_code, 201, enrollment.data)
        self.assertEqual(enrollment.data["assistance_modality"], Enrollment.AssistanceModality.NFI)
        approved = self.client.patch(f"/api/enrollments/{enrollment.data['id']}/", {"status": "APPROVED"}, format="json")
        self.assertEqual(approved.status_code, 200, approved.data)

        warehouse = Warehouse.objects.create(program=program, name="NFI warehouse", location="Khartoum", created_by=self.manager)
        item = NFIItem.objects.create(program=program, warehouse=warehouse, name="Kit", item_type="Food", unit="kit", initial_quantity=10, available_quantity=10, created_by=self.manager)
        entitlement = self.client.post(
            "/api/nfi-entitlements/",
            {"program": str(program.id), "beneficiary": str(beneficiary.id), "warehouse": str(warehouse.id), "item": str(item.id), "quantity": 2},
            format="json",
        )
        self.assertEqual(entitlement.status_code, 201, entitlement.data)

    def test_combined_program_allows_all_modalities(self):
        program = self.make_program("Cash and NFI", True, True)
        for index, modality in enumerate(("CASH", "NFI", "CASH_NFI"), start=1):
            response = self.enroll(program, self.make_beneficiary(program, f"BOTH-{index}"), modality)
            self.assertEqual(response.status_code, 201, response.data)
