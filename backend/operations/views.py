import csv
import io
import json
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from openpyxl import load_workbook
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import IdempotencyRecord, Tenant, User
from accounts.serializers import LoginSerializer, TenantSerializer, UserSerializer
from accounts.viewsets import RoleProtectedTenantViewSet, TenantScopedModelViewSet
from ai_services.complaint_ai import analyze_complaint
from assistance.models import Beneficiary, CashEntitlement, Enrollment, Household, HouseholdEligibility, HouseholdEnrollment
from assistance.serializer_helpers import national_id_digest, national_id_reference, normalize_national_id, normalize_phone_number
from assistance.serializers import BeneficiarySerializer, CashEntitlementSerializer, EnrollmentSerializer, HouseholdEligibilitySerializer, HouseholdEnrollmentSerializer, HouseholdSerializer
from automation.models import AISignal, AutomationExecution, AutomationRule, ReviewTask
from automation.serializers import AISignalSerializer, AutomationExecutionSerializer, AutomationRuleSerializer, ReviewTaskSerializer
from operations.inventory import _available_nfi_stock, _nfi_reserved_quantity, _reserve_nfi_entitlement_stock
from operations.models import Complaint, ComplaintAIAnalysis, DistributionAllocation, DistributionEvent, DistributionIssue, NFIEntitlement, NFIItem, PaymentDeliveryReview, StockMovement, Warehouse
from operations.serializers import ComplaintAIAnalysisSerializer, ComplaintSerializer, DistributionAllocationSerializer, DistributionEventSerializer, DistributionIssueSerializer, NFIEntitlementSerializer, NFIItemSerializer, PaymentDeliveryReviewSerializer, StockMovementSerializer, WarehouseSerializer
from payments.models import PaymentBatch, PaymentChannelConfig, PaymentEvent, PaymentInstruction, ReconciliationItem
from payments.serializers import PaymentBatchSerializer, PaymentChannelConfigSerializer, PaymentEventSerializer, PaymentInstructionSerializer, ReconciliationItemSerializer
from programs.models import ActivityDependency, Budget, Program, ProgramActivity
from programs.serializers import ActivityDependencySerializer, BudgetSerializer, ProgramActivitySerializer, ProgramSerializer
from programs.services import payment_budget_totals, scoped_program
from reporting.audit import audit, json_safe
from reporting.import_utils import normalize_import_header, normalize_import_rows
from reporting.models import AuditEvent, PDMResponse
from reporting.serializers import AuditEventSerializer
from services.platform import AI_AUTOMATION_ENABLED, run_anomaly_detection, run_automated_reconciliation, run_deduplication_check, verified_program_summary



class PaymentDeliveryReviewViewSet(RoleProtectedTenantViewSet):
    queryset = PaymentDeliveryReview.objects.select_related(
        "payment_instruction__cash_entitlement__program",
        "payment_instruction__cash_entitlement__household",
        "payment_instruction__channel_config",
        "household",
        "program",
        "reviewed_by",
    )
    serializer_class = PaymentDeliveryReviewSerializer
    tenant_field = "program__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER, User.Role.FINANCE, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER}

    def destroy(self, request, *args, **kwargs):
        raise PermissionDenied("Payment delivery reviews are audit records and cannot be deleted")

    @transaction.atomic
    def perform_create(self, serializer):
        instruction = PaymentInstruction.objects.select_for_update().get(
            pk=serializer.validated_data["payment_instruction"].pk
        )
        if not instruction.cash_entitlement_id:
            raise ValidationError({"payment_instruction": "A household cash entitlement is required"})
        entitlement = instruction.cash_entitlement
        if not self.request.user.is_superuser and entitlement.program.tenant_id != self.request.user.tenant_id:
            raise ValidationError({"payment_instruction": "Payment belongs to another tenant"})
        review = serializer.save(
            payment_instruction=instruction,
            household=entitlement.household,
            program=entitlement.program,
            reviewed_by=self.request.user,
            reviewed_at=timezone.now(),
        )
        audit(self.request.user, "PAYMENT_DELIVERY_REVIEW_CREATED", review, after=self.get_serializer(review).data, tenant=entitlement.program.tenant)

    @transaction.atomic
    def perform_update(self, serializer):
        current = PaymentDeliveryReview.objects.select_for_update().select_related("program__tenant").get(pk=serializer.instance.pk)
        requested_instruction = serializer.validated_data.get("payment_instruction", current.payment_instruction)
        if requested_instruction.pk != current.payment_instruction_id:
            raise ValidationError({"payment_instruction": "The reviewed payment cannot be changed"})
        before = self.get_serializer(current).data
        review = serializer.save(
            payment_instruction=current.payment_instruction,
            household=current.household,
            program=current.program,
            reviewed_by=self.request.user,
            reviewed_at=timezone.now(),
        )
        audit(self.request.user, "PAYMENT_DELIVERY_REVIEW_UPDATED", review, before=before, after=self.get_serializer(review).data, tenant=current.program.tenant)


class ComplaintViewSet(RoleProtectedTenantViewSet):
    queryset = Complaint.objects.select_related("beneficiary", "beneficiary__household")
    serializer_class = ComplaintSerializer
    tenant_field = "beneficiary__household__tenant"
    allowed_roles = {User.Role.ADMIN, User.Role.SUPPORT, User.Role.MANAGER}


def complaint_ai_scope(request, complaint_id):
    if request.user.role not in COMPLAINT_AI_READ_ROLES:
        raise PermissionDenied("Your role does not have permission for complaint AI analysis")
    complaints = Complaint.objects.select_related("beneficiary__household__program", "instruction")
    if not request.user.is_superuser:
        complaints = complaints.filter(beneficiary__household__tenant=request.user.tenant)
    return get_object_or_404(complaints, id=complaint_id)


@api_view(["POST"])
def complaint_ai_analyze_view(request, complaint_id):
    complaint = complaint_ai_scope(request, complaint_id)
    if request.user.role not in COMPLAINT_AI_WRITE_ROLES:
        raise PermissionDenied("Your role does not have permission to run complaint AI analysis")
    analysis = analyze_complaint(complaint, actor=request.user)
    audit(request.user, "COMPLAINT_AI_ANALYZED", analysis, after=ComplaintAIAnalysisSerializer(analysis).data, tenant=analysis.tenant)
    return Response(ComplaintAIAnalysisSerializer(analysis).data)


@api_view(["GET"])
def complaint_ai_analysis_view(request, complaint_id):
    complaint = complaint_ai_scope(request, complaint_id)
    analysis = get_object_or_404(ComplaintAIAnalysis, complaint=complaint)
    return Response(ComplaintAIAnalysisSerializer(analysis).data)


@api_view(["POST"])
def complaint_ai_review_view(request, complaint_id):
    complaint = complaint_ai_scope(request, complaint_id)
    if request.user.role not in COMPLAINT_AI_WRITE_ROLES:
        raise PermissionDenied("Your role does not have permission to review complaint AI analysis")
    decision = request.data.get("decision")
    if decision not in ComplaintAIAnalysis.ReviewDecision.values:
        raise ValidationError({"decision": "Use ACCEPT, MODIFY, or DISMISS."})
    analysis = get_object_or_404(ComplaintAIAnalysis, complaint=complaint)
    before = ComplaintAIAnalysisSerializer(analysis).data
    analysis.review_decision = decision
    analysis.reviewer_note = request.data.get("reviewer_note", "")
    analysis.reviewed_by = request.user
    analysis.reviewed_at = timezone.now()
    analysis.save(update_fields=["review_decision", "reviewer_note", "reviewed_by", "reviewed_at", "updated_at"])
    audit(request.user, "COMPLAINT_AI_REVIEWED", analysis, before=before, after=ComplaintAIAnalysisSerializer(analysis).data, tenant=analysis.tenant)
    return Response(ComplaintAIAnalysisSerializer(analysis).data)


@api_view(["POST"])
def complaint_ai_analyze_bulk_view(request):
    if request.user.role not in COMPLAINT_AI_WRITE_ROLES:
        raise PermissionDenied("Your role does not have permission to run complaint AI analysis")
    complaints = Complaint.objects.select_related("beneficiary__household__program", "instruction").filter(status=Complaint.Status.OPEN)
    if not request.user.is_superuser:
        complaints = complaints.filter(beneficiary__household__tenant=request.user.tenant)
    analyses = [analyze_complaint(complaint, actor=request.user) for complaint in complaints]
    audit(request.user, "COMPLAINT_AI_BULK_ANALYZED", request.user, after={"analyzed_count": len(analyses)}, tenant=request.user.tenant)
    return Response({"analyzed_count": len(analyses), "analyses": ComplaintAIAnalysisSerializer(analyses, many=True).data})


class WarehouseViewSet(RoleProtectedTenantViewSet):
    queryset = Warehouse.objects.select_related("program")
    serializer_class = WarehouseSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER, User.Role.FINANCE, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER}
    tenant_field = "program__tenant"

    def get_queryset(self):
        return super().get_queryset().filter(program__nfi_enabled=True)

    def perform_create(self, serializer):
        program = serializer.validated_data["program"]
        if not program.nfi_enabled:
            raise ValidationError("NFI is disabled for this program")
        serializer.save(created_by=self.request.user)


class NFIItemViewSet(RoleProtectedTenantViewSet):
    queryset = NFIItem.objects.select_related("program", "warehouse")
    serializer_class = NFIItemSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER, User.Role.FINANCE, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER}
    tenant_field = "program__tenant"

    def get_queryset(self):
        return super().get_queryset().filter(program__nfi_enabled=True)

    def perform_create(self, serializer):
        data = serializer.validated_data
        program, warehouse = data["program"], data["warehouse"]
        if not program.nfi_enabled:
            raise ValidationError("NFI is disabled for this program")
        if warehouse.program_id != program.id:
            raise ValidationError("Warehouse must belong to the selected program")
        quantity = data.get("initial_quantity", data.get("available_quantity", 0))
        instance = serializer.save(initial_quantity=quantity, available_quantity=quantity, created_by=self.request.user)
        if quantity:
            StockMovement.objects.create(program=program, warehouse=warehouse, item=instance, quantity=quantity, movement_type=StockMovement.MovementType.ENTRY, reference="initial stock", created_by=self.request.user)


class NFIEntitlementViewSet(RoleProtectedTenantViewSet):
    queryset = NFIEntitlement.objects.select_related("household", "beneficiary", "program", "item", "warehouse")
    serializer_class = NFIEntitlementSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER, User.Role.REVIEWER, User.Role.AUDITOR}
    write_roles = {User.Role.ADMIN, User.Role.FINANCE, User.Role.MANAGER}
    tenant_field = "program__tenant"

    def get_queryset(self):
        return super().get_queryset().filter(program__nfi_enabled=True)

    def perform_create(self, serializer):
        data = serializer.validated_data
        household, program, item = data["household"], data["program"], data["item"]
        if not program.nfi_enabled:
            raise ValidationError("NFI assistance is disabled for this program")
        warehouse = data.get("warehouse")
        if not warehouse:
            raise ValidationError("Warehouse is required for NFI entitlement")
        if item.program_id != program.id or warehouse.program_id != program.id or item.warehouse_id != warehouse.id or not item.active:
            raise ValidationError("Item is not active for this program")
        if household.eligibility_decisions.exists() and not household.eligibility_decisions.filter(program=program, status=HouseholdEligibility.Status.ELIGIBLE).exists():
            raise ValidationError("Household is not eligible for NFI assistance")
        if household.household_enrollments.exists() and not household.household_enrollments.filter(
            program=program,
            status__in=[HouseholdEnrollment.Status.ACCEPTED, HouseholdEnrollment.Status.APPROVED],
        ).exists():
            raise ValidationError("Household enrollment has not been approved")
        with transaction.atomic():
            Household.objects.select_for_update().get(pk=household.pk)
            locked = NFIItem.objects.select_for_update().get(pk=item.pk)
            if (
                locked.program_id != program.id
                or locked.warehouse_id != warehouse.id
                or warehouse.program_id != program.id
            ):
                raise ValidationError({"item": "Item, warehouse, and program must match"})
            available = _available_nfi_stock(locked)
            if data["quantity"] > available:
                raise ValidationError(
                    {
                        "quantity": (
                            f"Insufficient stock for {locked.name}. Available: "
                            f"{available}; requested: {data['quantity']}."
                        )
                    }
                )
            approved = HouseholdEnrollment.objects.select_for_update().filter(
                household_id=household.pk,
                program=program,
                status__in=[HouseholdEnrollment.Status.ACCEPTED, HouseholdEnrollment.Status.APPROVED],
                assistance_modality__in=[Enrollment.AssistanceModality.NFI, Enrollment.AssistanceModality.CASH_NFI],
            ).exists()
            if not approved:
                raise ValidationError({"household": "Household must have approved NFI enrollment"})
            locked.available_quantity -= data["quantity"]
            locked.save(update_fields=["available_quantity"])
            instance = serializer.save(created_by=self.request.user)
            StockMovement.objects.create(program=program, warehouse=locked.warehouse, item=locked, quantity=data["quantity"], movement_type=StockMovement.MovementType.ALLOCATION, reference=str(instance.pk), created_by=self.request.user)
            audit(self.request.user, "NFI_STOCK_ALLOCATED", instance, after={"warehouse": str(locked.warehouse_id), "item": str(locked.id), "quantity": data["quantity"]}, tenant=program.tenant)

    @transaction.atomic
    def perform_update(self, serializer):
        current = NFIEntitlement.objects.select_for_update().get(pk=serializer.instance.pk)
        if current.status == NFIEntitlement.Status.DISTRIBUTED:
            raise ValidationError("Distributed NFI entitlements cannot be edited; record a delivery correction instead.")
        item = NFIItem.objects.select_for_update().get(pk=current.item_id)
        data = serializer.validated_data
        for field, current_value in (
            ("household", current.household),
            ("program", current.program),
            ("item", current.item),
            ("warehouse", current.warehouse),
        ):
            if field in data and getattr(data[field], "pk", None) != getattr(current_value, "pk", None):
                raise ValidationError({field: "An entitlement's household, program, item, and warehouse cannot be changed."})
        new_quantity = serializer.validated_data.get("quantity", current.quantity)
        new_status = serializer.validated_data.get("status", current.status)
        delivered = current.issues.aggregate(total=Sum("actual_quantity"))["total"] or 0
        allocated = current.allocations.aggregate(total=Sum("allocated_quantity"))["total"] or 0
        if new_status == NFIEntitlement.Status.CANCELLED and (delivered or allocated):
            raise ValidationError({"status": "An allocated or delivered entitlement cannot be cancelled."})
        if new_quantity < max(delivered, allocated):
            raise ValidationError({"quantity": "Entitlement quantity cannot be less than its allocated or delivered quantity."})
        current_reserved = 0 if current.status == NFIEntitlement.Status.CANCELLED else max(current.quantity - delivered, 0)
        new_reserved = 0 if new_status == NFIEntitlement.Status.CANCELLED else max(new_quantity - delivered, 0)
        stock_change = new_reserved - current_reserved
        available_without_current = _available_nfi_stock(item, exclude_entitlement_id=current.pk)
        if new_reserved > available_without_current:
            raise ValidationError(
                {
                    "quantity": (
                        f"Insufficient stock for {item.name}. Available: "
                        f"{available_without_current}; requested: {new_reserved}."
                    )
                }
            )
        if stock_change > _available_nfi_stock(item):
            raise ValidationError(
                {
                    "quantity": (
                        f"Insufficient stock for {item.name}. Available: "
                        f"{_available_nfi_stock(item)}; requested: {stock_change}."
                    )
                }
            )
        item.available_quantity -= stock_change
        item.save(update_fields=["available_quantity", "updated_at"])
        before = self.get_serializer(current).data
        serializer.instance = current
        instance = serializer.save()
        if stock_change < 0:
            StockMovement.objects.create(program=current.program, warehouse=item.warehouse, item=item, quantity=-stock_change, movement_type=StockMovement.MovementType.RETURN, reference=str(instance.pk), created_by=self.request.user)
        elif stock_change > 0:
            StockMovement.objects.create(program=current.program, warehouse=item.warehouse, item=item, quantity=stock_change, movement_type=StockMovement.MovementType.ALLOCATION, reference=str(instance.pk), created_by=self.request.user)
        audit(self.request.user, "HOUSEHOLD_NFI_ENTITLEMENT_UPDATED", instance, before=before, after=self.get_serializer(instance).data, tenant=current.program.tenant)


class StockMovementViewSet(RoleProtectedTenantViewSet):
    queryset = StockMovement.objects.select_related("program", "warehouse", "item")
    serializer_class = StockMovementSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER, User.Role.AUDITOR, User.Role.FINANCE}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER}
    tenant_field = "program__tenant"


class DistributionEventViewSet(RoleProtectedTenantViewSet):
    queryset = DistributionEvent.objects.select_related("program", "warehouse")
    serializer_class = DistributionEventSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER, User.Role.AUDITOR, User.Role.FINANCE}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER}
    tenant_field = "program__tenant"

    def get_queryset(self):
        return super().get_queryset().filter(program__nfi_enabled=True)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        idempotency_fingerprint = sha256(json.dumps(request.data, sort_keys=True, default=str).encode("utf-8")).hexdigest() if idempotency_key else ""
        idempotency_record = None
        if idempotency_key:
            idempotency_record, created = IdempotencyRecord.objects.get_or_create(
                tenant=request.user.tenant,
                actor=request.user,
                endpoint=request.path,
                idempotency_key=idempotency_key,
                defaults={"request_fingerprint": idempotency_fingerprint, "response_body": {}},
            )
            idempotency_record = IdempotencyRecord.objects.select_for_update().get(pk=idempotency_record.pk)
            if not created:
                if idempotency_record.request_fingerprint != idempotency_fingerprint:
                    raise ValidationError({"idempotency_key": "This idempotency key was already used with different data."})
                return Response(idempotency_record.response_body, status=idempotency_record.response_status)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entitlement_ids = request.data.get("entitlement_ids") or []
        if not isinstance(entitlement_ids, list) or not entitlement_ids:
            raise ValidationError({"entitlement_ids": "Select one or more existing NFI entitlements"})
        program = serializer.validated_data["program"]
        warehouse = serializer.validated_data["warehouse"]
        # PostgreSQL does not allow FOR UPDATE on the nullable side of an
        # outer join. Household, beneficiary, and warehouse are transitional
        # nullable relations, so keep the locking query join-free and resolve
        # related objects normally below.
        entitlements = list(
            NFIEntitlement.objects.select_for_update().filter(
                id__in=entitlement_ids,
                program=program,
                warehouse=warehouse,
                status=NFIEntitlement.Status.ACTIVE,
            )
        )
        if len(entitlements) != len(set(str(item) for item in entitlement_ids)):
            raise ValidationError({"entitlement_ids": "Each entitlement must be active and belong to the selected program and warehouse"})
        locked_items = {
            item.pk: item
            for item in NFIItem.objects.select_for_update()
            .filter(pk__in={entitlement.item_id for entitlement in entitlements})
            .order_by("pk")
        }
        for entitlement in entitlements:
            item = locked_items[entitlement.item_id]
            if not item.active or item.program_id != program.id or item.warehouse_id != warehouse.id:
                raise ValidationError("Entitlement item is not active in the selected warehouse")
            if (
                not entitlement.household_id
                or entitlement.household.program_id != program.id
                or entitlement.household.tenant_id != program.tenant_id
            ):
                raise ValidationError("Every selected entitlement must belong to a registered household")
            if not HouseholdEnrollment.objects.select_for_update().filter(
                household_id=entitlement.household_id,
                program=program,
                status__in=[HouseholdEnrollment.Status.ACCEPTED, HouseholdEnrollment.Status.APPROVED],
                assistance_modality__in=[Enrollment.AssistanceModality.NFI, Enrollment.AssistanceModality.CASH_NFI],
            ).exists():
                raise ValidationError("Every selected household must have approved NFI enrollment")
            allocated = DistributionAllocation.objects.filter(entitlement=entitlement).aggregate(total=Sum("allocated_quantity"))["total"] or 0
            if allocated >= entitlement.quantity:
                raise ValidationError("An entitlement is already fully assigned to a distribution event")
            _reserve_nfi_entitlement_stock(entitlement, item, request.user)
        event = serializer.save(created_by=request.user)
        allocations = []
        for entitlement in entitlements:
            allocated = DistributionAllocation.objects.filter(entitlement=entitlement).aggregate(total=Sum("allocated_quantity"))["total"] or 0
            remaining = entitlement.quantity - allocated
            allocations.append(DistributionAllocation.objects.create(household=entitlement.household, beneficiary=entitlement.beneficiary, entitlement=entitlement, event=event, item=entitlement.item, planned_quantity=remaining, allocated_quantity=remaining, status="ALLOCATED", created_by=request.user))
        audit(request.user, "DISTRIBUTION_EVENT_CREATED", event, after={"entitlement_ids": [str(item.id) for item in entitlements], "allocation_ids": [str(item.id) for item in allocations]}, tenant=program.tenant)
        headers = self.get_success_headers(serializer.data)
        response = Response(self.get_serializer(event).data, status=status.HTTP_201_CREATED, headers=headers)
        if idempotency_record is not None:
            idempotency_record.response_status = response.status_code
            idempotency_record.response_body = json_safe(response.data)
            idempotency_record.save(update_fields=["response_status", "response_body"])
        return response

    def perform_create(self, serializer):
        program = serializer.validated_data["program"]
        warehouse = serializer.validated_data.get("warehouse")
        if not program.nfi_enabled:
            raise ValidationError("NFI assistance is disabled for this program")
        if not warehouse:
            raise ValidationError({"warehouse": "A warehouse is required for a distribution event"})
        if warehouse.program_id != program.id:
            raise ValidationError("Warehouse must belong to the selected program")
        serializer.save(created_by=self.request.user)


class DistributionAllocationViewSet(RoleProtectedTenantViewSet):
    queryset = DistributionAllocation.objects.select_related("beneficiary", "entitlement", "event__program", "event__warehouse", "item")
    serializer_class = DistributionAllocationSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER, User.Role.AUDITOR, User.Role.FINANCE}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER}
    tenant_field = "event__program__tenant"

    @transaction.atomic
    def perform_create(self, serializer):
        data = serializer.validated_data
        entitlement, event, item = data["entitlement"], data["event"], data["item"]
        entitlement = NFIEntitlement.objects.select_for_update().get(pk=entitlement.pk)
        household = entitlement.household
        event = DistributionEvent.objects.select_for_update().get(pk=event.pk)
        item = NFIItem.objects.select_for_update().get(pk=item.pk)
        if entitlement.household_id != household.id or entitlement.item_id != item.id or event.program_id != entitlement.program_id:
            raise ValidationError("Allocation must match the entitlement beneficiary, item, and program")
        if not event.warehouse_id or entitlement.warehouse_id != event.warehouse_id or item.warehouse_id != event.warehouse_id:
            raise ValidationError("Allocation warehouse must match the entitlement and event warehouse")
        if (
            household.program_id != event.program_id
            or household.tenant_id != event.program.tenant_id
            or not HouseholdEnrollment.objects.select_for_update().filter(
                household=household,
                program=event.program,
                status__in=[HouseholdEnrollment.Status.ACCEPTED, HouseholdEnrollment.Status.APPROVED],
                assistance_modality__in=[Enrollment.AssistanceModality.NFI, Enrollment.AssistanceModality.CASH_NFI],
            ).exists()
        ):
            raise ValidationError("Allocation requires an approved household NFI entitlement in this program")
        if entitlement.status != NFIEntitlement.Status.ACTIVE:
            raise ValidationError("Only active NFI entitlements can be allocated")
        if event.status in {DistributionEvent.Status.CLOSED, DistributionEvent.Status.CANCELLED}:
            raise ValidationError("Allocations cannot be added to a closed or cancelled event")
        planned = data["planned_quantity"]
        allocated = data.get("allocated_quantity", 0)
        already_allocated = DistributionAllocation.objects.filter(entitlement=entitlement).aggregate(total=Sum("allocated_quantity"))["total"] or 0
        if planned != allocated or allocated < 1 or already_allocated + allocated > entitlement.quantity:
            raise ValidationError("Allocation must use only the remaining entitlement quantity")
        _reserve_nfi_entitlement_stock(entitlement, item, self.request.user)
        serializer.save(
            entitlement=entitlement,
            event=event,
            item=item,
            household=household,
            beneficiary=entitlement.beneficiary,
            created_by=self.request.user,
        )

    @transaction.atomic
    def perform_update(self, serializer):
        entitlement = NFIEntitlement.objects.select_for_update().get(pk=serializer.instance.entitlement_id)
        current = DistributionAllocation.objects.select_for_update().get(pk=serializer.instance.pk)
        data = serializer.validated_data
        if (
            data.get("entitlement", entitlement).pk != entitlement.pk
            or data.get("event", current.event).pk != current.event_id
            or data.get("item", current.item).pk != current.item_id
            or getattr(data.get("household", current.household), "pk", None) != current.household_id
        ):
            raise ValidationError("An allocation's household, entitlement, event, and item cannot be changed")
        allocated = data.get("allocated_quantity", current.allocated_quantity)
        planned = data.get("planned_quantity", current.planned_quantity)
        already_allocated = DistributionAllocation.objects.filter(entitlement=entitlement).exclude(pk=current.pk).aggregate(total=Sum("allocated_quantity"))["total"] or 0
        if entitlement.status != NFIEntitlement.Status.ACTIVE or planned != allocated or allocated < 1 or already_allocated + allocated > entitlement.quantity:
            raise ValidationError("Allocation must use only the remaining active entitlement quantity")
        serializer.instance = current
        serializer.save()


class DistributionIssueViewSet(RoleProtectedTenantViewSet):
    queryset = DistributionIssue.objects.select_related("beneficiary", "entitlement", "event__program", "event__warehouse", "item")
    serializer_class = DistributionIssueSerializer
    allowed_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER, User.Role.AUDITOR, User.Role.FINANCE}
    write_roles = {User.Role.ADMIN, User.Role.MANAGER, User.Role.REVIEWER}
    tenant_field = "event__program__tenant"

    @transaction.atomic
    def perform_create(self, serializer):
        data = serializer.validated_data
        entitlement, event = data["entitlement"], data["event"]
        entitlement = NFIEntitlement.objects.select_for_update().get(pk=entitlement.pk)
        event = DistributionEvent.objects.select_for_update().get(pk=event.pk)
        allocation = DistributionAllocation.objects.select_for_update().filter(
            entitlement=entitlement,
            event=event,
        ).first()
        if not allocation:
            raise ValidationError("Delivery review requires an allocation from this distribution event")
        existing_issues = list(
            DistributionIssue.objects.select_for_update().filter(
                entitlement=entitlement,
                event=event,
            )
        )
        already_delivered = sum(issue.actual_quantity for issue in existing_issues)
        planned = data["planned_quantity"]
        actual = data["actual_quantity"]
        if event.program_id != entitlement.program_id or data["item"].id != entitlement.item_id or data["household"].id != entitlement.household_id:
            raise ValidationError("Distribution issue relationships must belong to the same entitlement and program")
        if (
            planned != allocation.allocated_quantity
            or actual > planned
            or planned < 1
            or already_delivered + actual > allocation.allocated_quantity
            or entitlement.status == NFIEntitlement.Status.CANCELLED
        ):
            raise ValidationError("Delivered quantity cannot exceed planned entitlement quantity")
        instance = serializer.save(
            entitlement=entitlement,
            event=event,
            household=entitlement.household,
            beneficiary=entitlement.beneficiary,
            item=entitlement.item,
            created_by=self.request.user,
        )
        movement_type = StockMovement.MovementType.DISTRIBUTION if instance.actual_quantity else StockMovement.MovementType.ISSUE
        if instance.actual_quantity:
            StockMovement.objects.create(program=event.program, warehouse=event.warehouse, item=instance.item, quantity=instance.actual_quantity, movement_type=movement_type, reference=str(instance.pk), created_by=self.request.user)
        delivered_total = DistributionIssue.objects.filter(entitlement=entitlement).aggregate(total=Sum("actual_quantity"))["total"] or 0
        if delivered_total >= entitlement.quantity:
            instance.entitlement.status = NFIEntitlement.Status.DISTRIBUTED
            instance.entitlement.save(update_fields=["status", "updated_at"])
        audit(self.request.user, "DISTRIBUTION_DELIVERY_RECORDED", instance, after=serializer.data)

    @transaction.atomic
    def perform_update(self, serializer):
        entitlement = NFIEntitlement.objects.select_for_update().get(pk=serializer.instance.entitlement_id)
        event = DistributionEvent.objects.select_for_update().get(pk=serializer.instance.event_id)
        allocation = DistributionAllocation.objects.select_for_update().filter(
            entitlement=entitlement,
            event=event,
        ).first()
        if not allocation:
            raise ValidationError("Delivery review requires an allocation from this distribution event")
        current = DistributionIssue.objects.select_for_update().get(pk=serializer.instance.pk)
        data = serializer.validated_data
        if (
            data.get("entitlement", entitlement).pk != entitlement.pk
            or data.get("event", event).pk != event.pk
            or getattr(data.get("household", current.household), "pk", None) != current.household_id
            or data.get("item", current.item).pk != current.item_id
        ):
            raise ValidationError("A delivery's household, entitlement, event, and item cannot be changed")
        other_issues = list(
            DistributionIssue.objects.select_for_update().filter(
                entitlement=entitlement,
                event=event,
            ).exclude(pk=current.pk)
        )
        already_delivered = sum(issue.actual_quantity for issue in other_issues)
        planned = data.get("planned_quantity", current.planned_quantity)
        actual = data.get("actual_quantity", current.actual_quantity)
        if (
            planned != allocation.allocated_quantity
            or actual > planned
            or planned < 1
            or already_delivered + actual > allocation.allocated_quantity
            or entitlement.status == NFIEntitlement.Status.CANCELLED
        ):
            raise ValidationError("Actual delivery cannot exceed the planned entitlement quantity")
        serializer.instance = current
        before = DistributionIssueSerializer(current).data
        old_actual = current.actual_quantity
        instance = serializer.save()
        delta = instance.actual_quantity - old_actual
        if delta > 0:
            StockMovement.objects.create(program=instance.event.program, warehouse=instance.event.warehouse, item=instance.item, quantity=delta, movement_type=StockMovement.MovementType.DISTRIBUTION, reference=str(instance.pk), created_by=self.request.user)
        delivered_total = DistributionIssue.objects.filter(entitlement=entitlement).aggregate(total=Sum("actual_quantity"))["total"] or 0
        if delivered_total >= entitlement.quantity and entitlement.status != NFIEntitlement.Status.DISTRIBUTED:
            instance.entitlement.status = NFIEntitlement.Status.DISTRIBUTED
            instance.entitlement.save(update_fields=["status", "updated_at"])
        elif delivered_total < entitlement.quantity and entitlement.status == NFIEntitlement.Status.DISTRIBUTED:
            instance.entitlement.status = NFIEntitlement.Status.ACTIVE
            instance.entitlement.save(update_fields=["status", "updated_at"])
        audit(self.request.user, "DISTRIBUTION_DELIVERY_UPDATED", instance, before=before, after=DistributionIssueSerializer(instance).data)


COMPLAINT_AI_READ_ROLES = {User.Role.ADMIN, User.Role.MANAGER, User.Role.SUPPORT}
COMPLAINT_AI_WRITE_ROLES = {User.Role.ADMIN, User.Role.MANAGER, User.Role.SUPPORT}
