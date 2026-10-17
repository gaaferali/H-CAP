from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Tenant, User
from assistance.models import Household, HouseholdEligibility, HouseholdModalityDecision
from operations.models import Warehouse
from programs.models import Program

from .models import (
    ControlledProductAuthorization,
    MedicalBatch,
    MedicalDistributionAllocation,
    MedicalDistributionIssue,
    MedicalEntitlement,
    MedicalProduct,
    MedicalStockMovement,
)


class MedicalWorkflowAPITests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Medical Tenant", tenant_type="NGO", default_currency="SDG")
        self.user = User.objects.create_user(
            email="medical-admin@example.test", full_name="Medical Admin", tenant=self.tenant,
            role=User.Role.ADMIN, password="test-password",
        )
        self.program = Program.objects.create(
            tenant=self.tenant, name="Medical-only", country="Sudan", country_code="SD",
            currency="SDG", cash_enabled=False, nfi_enabled=False, medical_enabled=True,
            status=Program.Status.ACTIVE,
            created_by=self.user,
        )
        self.client.force_authenticate(self.user)
        self.warehouse = Warehouse.objects.create(
            program=self.program, name="Central medical store", location="Khartoum",
            created_by=self.user,
        )
        self.household = Household.objects.create(
            tenant=self.tenant, program=self.program, household_size=5, location="Khartoum",
            registration_date=timezone.localdate(), registration_reference="HH-MED-1", created_by=self.user,
        )
        HouseholdEligibility.objects.create(
            household=self.household, program=self.program,
            status=HouseholdEligibility.Status.ELIGIBLE, decided_by=self.user, decided_at=timezone.now(),
        )
        HouseholdModalityDecision.objects.create(
            household=self.household, program=self.program,
            modality=HouseholdModalityDecision.Modality.MEDICAL,
            status=HouseholdModalityDecision.Status.APPROVED,
            decided_by=self.user, decided_at=timezone.now(),
        )
        self.product = MedicalProduct.objects.create(
            program=self.program, sku="AMOX-500", name="Amoxicillin 500 mg",
            category=MedicalProduct.Category.MEDICINE, unit_of_measurement="capsule",
            created_by=self.user,
        )

    def post(self, name, data, key):
        return self.client.post(reverse(name), data, format="json", HTTP_IDEMPOTENCY_KEY=key)

    def test_complete_medical_only_workflow_is_idempotent_and_readable(self):
        earlier = self.post("medical-batch-list", {
            "program": str(self.program.pk), "product": str(self.product.pk),
            "warehouse": str(self.warehouse.pk), "lot_number": "LOT-EARLY",
            "expiry_date": str(timezone.localdate() + timedelta(days=60)),
            "received_quantity": 10, "supplier_source": "Approved supplier",
        }, "receive-earlier")
        self.assertEqual(earlier.status_code, status.HTTP_201_CREATED, earlier.data)
        replay = self.post("medical-batch-list", {
            "program": str(self.program.pk), "product": str(self.product.pk),
            "warehouse": str(self.warehouse.pk), "lot_number": "LOT-EARLY",
            "expiry_date": str(timezone.localdate() + timedelta(days=60)),
            "received_quantity": 10, "supplier_source": "Approved supplier",
        }, "receive-earlier")
        self.assertEqual(replay.status_code, status.HTTP_201_CREATED)
        self.assertEqual(MedicalBatch.objects.filter(lot_number="LOT-EARLY").count(), 1)

        later = self.post("medical-batch-list", {
            "program": str(self.program.pk), "product": str(self.product.pk),
            "warehouse": str(self.warehouse.pk), "lot_number": "LOT-LATER",
            "expiry_date": str(timezone.localdate() + timedelta(days=120)),
            "received_quantity": 10, "supplier_source": "Approved supplier",
        }, "receive-later")
        self.assertEqual(later.status_code, status.HTTP_201_CREATED, later.data)

        entitlement = self.post("medical-entitlement-list", {
            "program": str(self.program.pk), "recipient_type": "HOUSEHOLD",
            "household": str(self.household.pk), "product": str(self.product.pk),
            "quantity": 3, "warehouse": str(self.warehouse.pk), "notes": "Prescribed course",
        }, "medical-entitlement")
        self.assertEqual(entitlement.status_code, status.HTTP_201_CREATED, entitlement.data)
        entitlement_id = entitlement.data["id"]
        self.assertEqual(entitlement.data["program_name"], "Medical-only")
        self.assertEqual(entitlement.data["product_name"], "Amoxicillin 500 mg")
        self.assertEqual(entitlement.data["warehouse_name"], "Central medical store")
        self.assertEqual(entitlement.data["household_reference"], "HH-MED-1")

        approved = self.client.post(
            reverse("medical-entitlement-approve", args=[entitlement_id]), {}, format="json",
            HTTP_IDEMPOTENCY_KEY="approve-entitlement",
        )
        self.assertEqual(approved.status_code, status.HTTP_200_OK, approved.data)
        allocated = self.client.post(
            reverse("medical-entitlement-allocate", args=[entitlement_id]), {}, format="json",
            HTTP_IDEMPOTENCY_KEY="allocate-entitlement",
        )
        self.assertEqual(allocated.status_code, status.HTTP_200_OK, allocated.data)
        allocated_replay = self.client.post(
            reverse("medical-entitlement-allocate", args=[entitlement_id]), {}, format="json",
            HTTP_IDEMPOTENCY_KEY="allocate-entitlement",
        )
        self.assertEqual(allocated_replay.status_code, status.HTTP_200_OK)
        entitlement_record = MedicalEntitlement.objects.get(pk=entitlement_id)
        self.assertEqual(entitlement_record.batch.lot_number, "LOT-EARLY")
        self.assertEqual(MedicalBatch.objects.get(lot_number="LOT-EARLY").available_quantity, 7)
        self.assertEqual(
            MedicalStockMovement.objects.filter(
                movement_type=MedicalStockMovement.MovementType.ALLOCATION,
                reference=entitlement_id,
            ).count(), 1,
        )

        event = self.post("distribution-event-list", {
            "program": str(self.program.pk), "warehouse": str(self.warehouse.pk),
            "location": "Clinic A", "event_date": str(timezone.localdate()),
            "distribution_team": "Health team", "status": "OPEN", "entitlement_ids": [],
        }, "medical-event")
        self.assertEqual(event.status_code, status.HTTP_201_CREATED, event.data)
        allocation = self.post("medical-distribution-allocation-list", {
            "entitlement": entitlement_id, "event": event.data["id"],
            "batch": str(entitlement_record.batch_id), "quantity": 3,
        }, "medical-allocation")
        self.assertEqual(allocation.status_code, status.HTTP_201_CREATED, allocation.data)
        issue = self.post("medical-distribution-issue-list", {
            "allocation": allocation.data["id"], "actual_quantity": 3,
            "delivery_status": "RECEIVED", "distribution_date": str(timezone.localdate()),
            "evidence": "Signed receipt",
        }, "medical-issue")
        self.assertEqual(issue.status_code, status.HTTP_201_CREATED, issue.data)
        self.assertEqual(issue.data["product_name"], "Amoxicillin 500 mg")
        self.assertEqual(MedicalEntitlement.objects.get(pk=entitlement_id).status, MedicalEntitlement.Status.DISTRIBUTED)
        self.assertEqual(MedicalDistributionIssue.objects.count(), 1)

    def test_medical_only_program_needs_no_cash_budget_and_new_warehouse_is_immediately_listed(self):
        program = self.client.post("/api/programs/", {
            "name": "Mobile medical response", "country": "Sudan", "country_code": "SD",
            "currency": "SDG", "reporting_currency": "SDG", "exchange_rate": "1",
            "status": "DRAFT", "cash_enabled": False, "nfi_enabled": False,
            "medical_enabled": True, "workflow_config": {"payment_enabled": False}, "country_pack": {},
        }, format="json", HTTP_IDEMPOTENCY_KEY="medical-program-create")
        self.assertEqual(program.status_code, status.HTTP_201_CREATED, program.data)
        self.assertFalse(hasattr(Program.objects.get(pk=program.data["id"]), "budget"))
        warehouse = self.client.post("/api/warehouses/", {
            "program": program.data["id"], "name": "Mobile medical store",
            "location": "Port Sudan", "active": True,
        }, format="json", HTTP_IDEMPOTENCY_KEY="medical-warehouse-create")
        self.assertEqual(warehouse.status_code, status.HTTP_201_CREATED, warehouse.data)
        listing = self.client.get("/api/warehouses/")
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertIn("Mobile medical store", [row["name"] for row in listing.data["results"]])

    def test_disabled_program_and_unapproved_medical_modality_are_rejected(self):
        payment_program = Program.objects.create(
            tenant=self.tenant, name="Cash only", country="Sudan", country_code="SD", currency="SDG",
            cash_enabled=True, nfi_enabled=False, medical_enabled=False,
            status=Program.Status.ACTIVE, created_by=self.user,
        )
        response = self.post("medical-product-list", {
            "program": str(payment_program.pk), "sku": "NO-MED", "name": "Not allowed",
            "category": "MEDICINE", "unit_of_measurement": "tablet",
        }, "disabled-medical-product")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        HouseholdModalityDecision.objects.filter(household=self.household, program=self.program).update(status="REJECTED")
        response = self.post("medical-entitlement-list", {
            "program": str(self.program.pk), "recipient_type": "HOUSEHOLD",
            "household": str(self.household.pk), "product": str(self.product.pk),
            "quantity": 1, "warehouse": str(self.warehouse.pk),
        }, "unapproved-entitlement")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("approved Medical Assistance enrollment", str(response.data))

    def test_controlled_receipt_requires_explicit_authorization_and_expired_stock_is_rejected(self):
        controlled = MedicalProduct.objects.create(
            program=self.program, sku="CTRL-1", name="Controlled medicine",
            category=MedicalProduct.Category.MEDICINE, unit_of_measurement="vial",
            controlled=True, created_by=self.user,
        )
        payload = {
            "program": str(self.program.pk), "product": str(controlled.pk),
            "warehouse": str(self.warehouse.pk), "lot_number": "CTRL-LOT",
            "expiry_date": str(timezone.localdate() + timedelta(days=90)),
            "received_quantity": 2, "supplier_source": "Licensed supplier",
        }
        denied = self.post("medical-batch-list", payload, "controlled-denied")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        ControlledProductAuthorization.objects.create(
            program=self.program, user=self.user, can_receive=True, can_issue=True,
            permit_reference="PERMIT-1", valid_from=timezone.localdate(), active=True,
            granted_by=self.user,
        )
        accepted = self.post("medical-batch-list", payload, "controlled-accepted")
        self.assertEqual(accepted.status_code, status.HTTP_201_CREATED, accepted.data)
        expired = dict(payload, lot_number="EXPIRED", expiry_date=str(timezone.localdate() - timedelta(days=1)))
        rejected = self.post("medical-batch-list", expired, "expired-rejected")
        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)

    def test_tenant_isolation_for_lists(self):
        other_tenant = Tenant.objects.create(name="Other", tenant_type="NGO", default_currency="SDG")
        other_user = User.objects.create_user(
            email="other@example.test", full_name="Other Admin", tenant=other_tenant,
            role=User.Role.ADMIN, password="test-password",
        )
        other_program = Program.objects.create(
            tenant=other_tenant, name="Other medical", country="Sudan", country_code="SD", currency="SDG",
            cash_enabled=False, nfi_enabled=False, medical_enabled=True, created_by=other_user,
        )
        MedicalProduct.objects.create(
            program=other_program, sku="OTHER", name="Other product", category="MEDICINE",
            unit_of_measurement="unit", created_by=other_user,
        )
        response = self.client.get(reverse("medical-product-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [row["name"] for row in response.data["results"]]
        self.assertEqual(names, ["Amoxicillin 500 mg"])

    def test_warehouse_transfer_is_balanced_and_idempotent(self):
        received = self.post("medical-batch-list", {
            "program": str(self.program.pk), "product": str(self.product.pk),
            "warehouse": str(self.warehouse.pk), "lot_number": "TRANSFER-LOT",
            "expiry_date": str(timezone.localdate() + timedelta(days=90)),
            "received_quantity": 8, "supplier_source": "Approved supplier",
        }, "transfer-source")
        self.assertEqual(received.status_code, status.HTTP_201_CREATED, received.data)
        destination = Warehouse.objects.create(
            program=self.program, name="Clinic store", location="Omdurman", created_by=self.user,
        )
        url = reverse("medical-batch-transfer", args=[received.data["id"]])
        payload = {"destination_warehouse": str(destination.pk), "quantity": 3, "reason": "Clinic replenishment"}
        first = self.client.post(url, payload, format="json", HTTP_IDEMPOTENCY_KEY="batch-transfer")
        replay = self.client.post(url, payload, format="json", HTTP_IDEMPOTENCY_KEY="batch-transfer")
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(MedicalBatch.objects.get(pk=received.data["id"]).available_quantity, 5)
        self.assertEqual(MedicalBatch.objects.get(warehouse=destination, lot_number="TRANSFER-LOT").available_quantity, 3)
        self.assertEqual(MedicalStockMovement.objects.filter(reference__startswith="TRANSFER-").count(), 2)
