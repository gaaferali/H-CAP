import csv
import io
import json
import uuid
import zipfile
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256

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
from assistance.models import Beneficiary, CashEntitlement, Enrollment, Household, HouseholdEligibility, HouseholdEnrollment
from assistance.serializer_helpers import household_reference, national_id_digest, national_id_reference, normalize_national_id, normalize_phone_number
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
from programs.services import payment_budget_totals, require_program_active, scoped_program
from reporting.audit import audit, json_safe
from reporting.import_utils import normalize_import_header, normalize_import_rows
from reporting.models import AuditEvent, PDMResponse
from reporting.serializers import AuditEventSerializer
from automation.ai_source import AI_AUTOMATION_ENABLED, run_deduplication_check
from medical.models import MedicalAlert, MedicalBatch, MedicalDistributionIssue, MedicalEntitlement, MedicalEquipment, MedicalProduct



# Returns tenant-scoped audit history without allowing edits or deletion.
class AuditEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditEventSerializer

    def get_queryset(self):
        if self.request.user.is_superuser:
            return AuditEvent.objects.all()
        if self.request.user.role not in {User.Role.ADMIN, User.Role.MANAGER, User.Role.AUDITOR}:
            raise PermissionDenied("Audit history is restricted to tenant administrators, managers, and auditors")
        return AuditEvent.objects.filter(tenant=self.request.user.tenant)


# Builds the role-based dashboard totals returned to the frontend.
@api_view(["GET"])
def reports_view(request):
    requested_tenant = request.query_params.get("tenant")
    if request.user.is_superuser:
        tenant = get_object_or_404(Tenant, id=requested_tenant) if requested_tenant else None
        programs = Program.objects.filter(tenant=tenant) if tenant else Program.objects.all()
    else:
        if requested_tenant and requested_tenant != str(request.user.tenant_id):
            raise PermissionDenied("You cannot access another tenant's dashboard")
        tenant = request.user.tenant
        programs = Program.objects.filter(tenant=tenant)
    requested_program = request.query_params.get("program")
    selected_program = None
    if requested_program:
        selected_program = get_object_or_404(programs, id=requested_program)
        programs = programs.filter(id=selected_program.id)
    households = Household.objects.filter(program__in=programs)
    payments = PaymentInstruction.objects.filter(
        Q(cash_entitlement__program__in=programs) | Q(enrollment__program__in=programs)
    )
    enrollments = Enrollment.objects.filter(program__in=programs)
    complaints = Complaint.objects.filter(beneficiary__household__program__in=programs)
    beneficiaries = Beneficiary.objects.filter(household__program__in=programs)
    household_eligibility = HouseholdEligibility.objects.filter(program__in=programs)
    household_enrollment = HouseholdEnrollment.objects.filter(program__in=programs)
    nfi_entitlements = NFIEntitlement.objects.filter(program__in=programs)
    nfi_issues = DistributionIssue.objects.filter(entitlement__in=nfi_entitlements)
    budgets = Budget.objects.filter(program__in=programs)
    distributed = payments.filter(status=PaymentInstruction.Status.SUCCESS).aggregate(total=Sum("amount"))["total"] or 0
    approved = enrollments.filter(status=Enrollment.Status.APPROVED).count()
    approved_amount = CashEntitlement.objects.filter(
        program__in=programs,
        status__in=[CashEntitlement.Status.ACTIVE, CashEntitlement.Status.PAID],
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    budget_totals = budgets.aggregate(planned=Sum("planned_total"), actual=Sum("actual_total"))
    dashboards = {
        "beneficiaries": beneficiaries.count(),
        "approvals": approved,
        "paid": payments.filter(status=PaymentInstruction.Status.SUCCESS).count(),
        "pending": payments.filter(status__in=[PaymentInstruction.Status.DRAFT, PaymentInstruction.Status.CREATED, PaymentInstruction.Status.SUBMITTED]).count(),
        "failed": payments.filter(status=PaymentInstruction.Status.FAILED).count(),
        "amounts_approved": str(approved_amount),
        "amounts_distributed": str(distributed),
        "budget_planned": str(budget_totals["planned"] or 0),
        "budget_actual": str(budget_totals["actual"] or 0),
        "budget_remaining": str((budget_totals["planned"] or 0) - (budget_totals["actual"] or 0)),
        "geography": list(households.values("location").annotate(count=Count("id")).order_by("location")),
        "reconciliation": list(payments.values("status").annotate(count=Count("id"), amount=Sum("amount")).order_by("status")),
        "complaints": list(complaints.values("status").annotate(count=Count("id")).order_by("status")),
        "assigned_complaints": list(complaints.filter(assigned_to=request.user).values("id", "category", "severity", "status", "resolution_notes", "beneficiary__full_name", "beneficiary__household__program__name").order_by("status", "-created_at")),
        "operational_exceptions": payments.filter(Q(status=PaymentInstruction.Status.FAILED) | Q(complaints__isnull=False)).distinct().count(),
        "eligible_households": household_eligibility.filter(status=HouseholdEligibility.Status.ELIGIBLE).values("household_id").distinct().count(),
        "enrolled_households": household_enrollment.filter(status__in=[HouseholdEnrollment.Status.ACCEPTED, HouseholdEnrollment.Status.APPROVED]).values("household_id").distinct().count(),
        "payment_beneficiaries": enrollments.filter(status=Enrollment.Status.APPROVED, assistance_modality__in=[Enrollment.AssistanceModality.CASH, Enrollment.AssistanceModality.CASH_NFI]).values("beneficiary_id").distinct().count(),
        "nfi_beneficiaries": enrollments.filter(status=Enrollment.Status.APPROVED, assistance_modality__in=[Enrollment.AssistanceModality.NFI, Enrollment.AssistanceModality.CASH_NFI]).values("beneficiary_id").distinct().count(),
        "nfi_entitlement_quantity": nfi_entitlements.aggregate(total=Sum("quantity"))["total"] or 0,
        "nfi_distributed_quantity": nfi_issues.aggregate(total=Sum("actual_quantity"))["total"] or 0,
        "nfi_pending_delivery": nfi_entitlements.filter(issues__isnull=True).count(),
        "available_nfi_stock": NFIItem.objects.filter(program__in=programs).aggregate(total=Sum("available_quantity"))["total"] or 0,
        "medical_products": MedicalProduct.objects.filter(program__in=programs, active=True).count(),
        "medical_available_stock": MedicalBatch.objects.filter(program__in=programs, status=MedicalBatch.Status.AVAILABLE).aggregate(total=Sum("available_quantity"))["total"] or 0,
        "medical_pending_entitlements": MedicalEntitlement.objects.filter(program__in=programs, status=MedicalEntitlement.Status.PENDING).count(),
        "medical_distributed_quantity": MedicalDistributionIssue.objects.filter(allocation__entitlement__program__in=programs).aggregate(total=Sum("actual_quantity"))["total"] or 0,
        "medical_open_alerts": MedicalAlert.objects.filter(program__in=programs, status=MedicalAlert.Status.OPEN).count(),
        "medical_equipment": MedicalEquipment.objects.filter(program__in=programs, active=True).count(),
        "program_kpis": list(programs.values("id", "name", "country", "currency", "status", "tenant__name").annotate(enrollments=Count("enrollments"))),
    }
    role_dashboard_fields = {
        User.Role.FIELD_OFFICER: {"beneficiaries", "approvals"},
        User.Role.SUPPORT: {"beneficiaries", "complaints", "assigned_complaints", "operational_exceptions"},
        User.Role.REVIEWER: {"beneficiaries", "approvals", "failed", "operational_exceptions"},
        User.Role.FINANCE: {
            "beneficiaries", "approvals", "paid", "pending", "failed", "amounts_approved",
            "amounts_distributed", "budget_planned", "budget_actual", "budget_remaining",
            "reconciliation", "operational_exceptions", "program_kpis",
            "medical_products", "medical_available_stock", "medical_pending_entitlements",
            "medical_distributed_quantity", "medical_open_alerts", "medical_equipment",
        },
    }
    if request.user.is_superuser or request.user.role in {User.Role.ADMIN, User.Role.MANAGER, User.Role.AUDITOR}:
        permitted_dashboards = dashboards
    else:
        permitted_fields = role_dashboard_fields.get(request.user.role, set())
        permitted_dashboards = {field: dashboards[field] for field in permitted_fields}
    if request.user.is_superuser:
        permitted_dashboards["tenant_count"] = Tenant.objects.count() if not tenant else 1
        permitted_dashboards["system_administrator"] = True
    return Response({
        "scope": {
            "tenant_id": str(tenant.id) if tenant else None,
            "tenant_name": tenant.name if tenant else "All tenants",
            "program_id": str(selected_program.id) if selected_program else None,
            "program_name": selected_program.name if selected_program else "All programs",
        },
        "dashboards": permitted_dashboards,
    })


# Returns operational and financial totals for one active program.
@api_view(["GET"])
def program_summary_view(request, program_id):
    program = scoped_program(request, program_id)
    require_program_active(program, "record import")
    payments = PaymentInstruction.objects.filter(
        Q(cash_entitlement__program=program) | Q(enrollment__program=program)
    )
    enrollments = Enrollment.objects.filter(program=program)
    complaints = Complaint.objects.filter(beneficiary__household__program=program)
    reconciliation = ReconciliationItem.objects.filter(program=program)
    budget = getattr(program, "budget", None)
    financials = payment_budget_totals(program)
    entitlements = CashEntitlement.objects.filter(program=program)
    return Response({
        "program": {"id": str(program.id), "name": program.name, "tenant_id": str(program.tenant_id), "tenant_name": program.tenant.name, "country_code": program.country_code, "currency": program.currency, "reporting_currency": program.reporting_currency, "timezone": program.timezone, "language": program.language},
        "beneficiaries": Beneficiary.objects.filter(household__program=program).count(),
        "eligibility": list(enrollments.values("eligibility_status").annotate(count=Count("id")).order_by("eligibility_status")),
        "approvals": enrollments.filter(status=Enrollment.Status.APPROVED).count(),
        "payments": list(payments.values("status").annotate(count=Count("id"), amount=Sum("amount")).order_by("status")),
        "amounts": {
            "entitled": str(entitlements.filter(status__in=[CashEntitlement.Status.ACTIVE, CashEntitlement.Status.PAID]).aggregate(total=Sum("amount"))["total"] or 0),
            "distributed": str(financials["funding"]),
            "fees": str(financials["fees"]),
            "total_budget_used": str(financials["used"]),
        },
        "failures": payments.filter(status=PaymentInstruction.Status.FAILED).count(),
        "reconciliation": list(reconciliation.values("issue_type", "status").annotate(count=Count("id")).order_by("issue_type")),
        "pdm": {"summary_source": "derived_api"},
        "complaints": list(complaints.values("status", "severity").annotate(count=Count("id")).order_by("status")),
        "budget": {
            "budget_amount": str(financials["budget_amount"]),
            "committed_amount": str(financials["committed"]),
            "successful_funding": str(financials["funding"]),
            "successful_fees": str(financials["fees"]),
            "used_amount": str(financials["used"]),
            "remaining_amount": str(financials["remaining"]),
            "status": budget.status,
        } if budget else None,
        "verified": True,
    })


@api_view(["POST"])
def imports_view(request):
    if request.user.role not in {User.Role.FIELD_OFFICER, User.Role.MANAGER} and not request.user.is_superuser:
        raise PermissionDenied("Only field officers and tenant managers can import mapped records")
    upload = request.FILES.get("file")
    program_id = request.data.get("program_id")
    confirmation_token = request.data.get("confirmation_token")
    if not upload or not program_id:
        raise ValidationError({"file": "A CSV or XLSX file is required", "program_id": "Required"})
    if upload.size > 10 * 1024 * 1024:
        raise ValidationError({"file": "Maximum file size is 10 MB"})
    program = scoped_program(request, program_id)
    tenant = program.tenant
    content = upload.read()
    content_hash = sha256(content).hexdigest()
    if upload.name.lower().endswith(".csv"):
        if b"\x00" in content:
            raise ValidationError({"file": "The CSV file contains invalid binary content."})
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    elif upload.name.lower().endswith(".xlsx"):
        if not content.startswith(b"PK"):
            raise ValidationError({"file": "The XLSX file signature is invalid."})
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sum(entry.file_size for entry in archive.infolist()) > 50 * 1024 * 1024:
                    raise ValidationError({"file": "The expanded spreadsheet is too large."})
        except zipfile.BadZipFile as exc:
            raise ValidationError({"file": "The XLSX file is damaged or invalid."}) from exc
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheet = workbook.active
        values = list(sheet.values)
        headers = [str(value).strip() if value is not None else "" for value in values[:1][0]] if values else []
        rows = [dict(zip(headers, row)) for row in values[1:] if any(value is not None and str(value).strip() for value in row)]
    else:
        raise ValidationError({"file": "Use a .csv or .xlsx file"})
    if len(rows) > 5000:
        raise ValidationError({"file": "Import files are limited to 5,000 data rows."})
    rows = normalize_import_rows(rows)
    available_columns = set(rows[0].keys()) if rows else set()
    required_columns = {"client_generated_id", "household_size", "location", "full_name"}
    missing_columns = sorted(required_columns - available_columns)
    if not ({"national_id", "beneficiary_number"} & available_columns):
        missing_columns.append("national_id (or beneficiary_number)")
    if missing_columns:
        raise ValidationError({"file": f"Missing required columns: {', '.join(missing_columns)}"})
    errors, normalized_rows = [], []
    for row_number, row in enumerate(rows, start=2):
        client_id = str(row.get("client_generated_id") or "").strip()
        raw_national_id = str(row.get("national_id") or row.get("beneficiary_number") or "").strip()
        full_name = str(row.get("full_name") or "").strip()
        location = str(row.get("location") or "").strip()
        try:
            household_size = int(row.get("household_size"))
            if household_size < 1:
                raise ValueError
        except (TypeError, ValueError):
            errors.append({"row": row_number, "field": "household_size", "message": "Use a positive whole number"})
            continue
        if not client_id or not raw_national_id or not full_name or not location:
            errors.append({"row": row_number, "field": "required", "message": "client_generated_id, location, national_id (or beneficiary_number), and full_name are required"})
            continue
        try:
            normalized_national_id = normalize_national_id(raw_national_id)
            phone_number = normalize_phone_number(row.get("phone_number") or row.get("phone_last4"))
        except ValidationError as exc:
            errors.append({"row": row_number, "field": "beneficiary", "message": str(exc.detail[0])})
            continue
        verification_status = str(row.get("verification_status") or Beneficiary.VerificationStatus.PENDING).upper()
        if verification_status not in Beneficiary.VerificationStatus.values:
            errors.append({"row": row_number, "field": "verification_status", "message": "Use PENDING, VERIFIED, or REJECTED"})
            continue
        normalized_rows.append({"client_id": client_id, "size": household_size, "location": location, "registration_date": str(row.get("registration_date") or timezone.localdate()), "number": national_id_reference(normalized_national_id), "full_name": full_name, "gender": str(row.get("gender") or "").strip(), "phone_number": phone_number, "phone_last4": phone_number[-4:] if phone_number else "", "national_id_hash": str(row.get("national_id_hash") or national_id_digest(normalized_national_id)).strip(), "consent_given": str(row.get("consent_given") or "").lower() in {"1", "true", "yes", "y"}, "verification_status": verification_status})
    duplicate_candidates = 0
    for row in normalized_rows:
        matches = Beneficiary.objects.filter(household__tenant=tenant, household__program=program)
        if row["national_id_hash"]:
            matches = matches.filter(national_id_hash=row["national_id_hash"])
        elif row["phone_number"]:
            matches = matches.filter(phone_number__endswith=row["phone_number"][-4:])
        else:
            continue
        duplicate_candidates += matches.count()
    if not confirmation_token:
        token = signing.dumps({"user_id": str(request.user.id), "tenant_id": str(tenant.id), "program_id": str(program.id), "content_hash": content_hash})
        return Response({
            "status": "ready_for_confirmation" if normalized_rows else "invalid",
            "rows_received": len(rows),
            "rows_valid": len(normalized_rows),
            "rows_invalid": len(errors),
            "errors": errors,
            "preview_rows": normalized_rows[:25],
            "possible_duplicate_matches": duplicate_candidates,
            "confirmation_token": token if normalized_rows else None,
            "client_identifiers_preserved": True,
        })
    try:
        confirmation = signing.loads(confirmation_token, max_age=15 * 60)
    except signing.BadSignature as exc:
        raise ValidationError({"confirmation_token": "Preview confirmation has expired or is invalid. Preview the file again."}) from exc
    if confirmation != {"user_id": str(request.user.id), "tenant_id": str(tenant.id), "program_id": str(program.id), "content_hash": content_hash}:
        raise ValidationError({"confirmation_token": "This confirmation does not match the selected program and file."})
    if not normalized_rows:
        raise ValidationError({"file": "There are no valid rows to import."})
    households_created = beneficiaries_created = duplicate_flags = 0
    with transaction.atomic():
        for row in normalized_rows:
            household, created = Household.objects.get_or_create(tenant=tenant, registration_reference=row["client_id"], defaults={"program": program, "household_size": row["size"], "location": row["location"], "registration_date": row["registration_date"], "created_by": request.user})
            if household.program_id != program.id:
                raise ValidationError({"client_generated_id": f"{row['client_id']} belongs to another program"})
            beneficiary, beneficiary_created = Beneficiary.objects.update_or_create(household=household, number=row["number"], defaults={"full_name": row["full_name"], "gender": row["gender"], "phone_number": row["phone_number"], "national_id_hash": row["national_id_hash"], "consent_given": row["consent_given"], "verification_status": row["verification_status"], "created_by": request.user})
            households_created += int(created)
            beneficiaries_created += int(beneficiary_created)
            if AI_AUTOMATION_ENABLED:
                duplicate_flags += int(run_deduplication_check(beneficiary)["matches"] > 0)
    audit(request.user, "IMPORT_COMPLETED", program, after={"rows": len(normalized_rows), "rows_invalid": len(errors), "households_created": households_created, "beneficiaries_created": beneficiaries_created}, tenant=tenant)
    return Response({
        "status": "completed",
        "rows_received": len(rows),
        "rows_invalid": len(errors),
        "errors": errors,
        "households_created": households_created,
        "beneficiaries_created": beneficiaries_created,
        "duplicate_flags": duplicate_flags,
        "client_identifiers_preserved": True,
    }, status=status.HTTP_201_CREATED)


# Calculates PDM coverage, complaints, payment amounts, and satisfaction totals.
def pdm_program_metrics(program, responses=None):
    successful_payments = PaymentInstruction.objects.filter(
        Q(cash_entitlement__program=program) | Q(enrollment__program=program),
        status=PaymentInstruction.Status.SUCCESS,
    )
    paid_beneficiaries = (
        successful_payments.filter(cash_entitlement__isnull=False)
        .values("cash_entitlement__household_id").distinct().count()
        + successful_payments.filter(cash_entitlement__isnull=True, beneficiary__isnull=False)
        .values("beneficiary_id").distinct().count()
    )
    distributed_amount = successful_payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    recorded_recipients = (responses or PDMResponse.objects.filter(program=program)).aggregate(total=Sum("received_count"))["total"] or 0
    complaints = Complaint.objects.filter(beneficiary__household__program=program).count()
    return {
        "paid_beneficiaries": paid_beneficiaries,
        "distributed_amount": distributed_amount,
        "recorded_recipients": recorded_recipients,
        "remaining_recipients": max(paid_beneficiaries - recorded_recipients, 0),
        "complaints": complaints,
        "complaint_rate": (Decimal(complaints) * Decimal("100") / Decimal(paid_beneficiaries)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if paid_beneficiaries else Decimal("0"),
    }


# Lists PDM responses or creates recipient feedback from verified payment data.
@api_view(["GET", "POST"])
def pdm_view(request):
    permitted_roles = {User.Role.ADMIN, User.Role.SUPPORT, User.Role.FIELD_OFFICER, User.Role.MANAGER, User.Role.AUDITOR}
    if request.user.role not in permitted_roles:
        raise PermissionDenied("Your role does not have permission for PDM")
    if request.method == "GET":
        responses = PDMResponse.objects.all() if request.user.is_superuser else PDMResponse.objects.filter(tenant=request.user.tenant)
        requested_tenant = request.query_params.get("tenant")
        if requested_tenant:
            if not request.user.is_superuser and requested_tenant != str(request.user.tenant_id):
                raise PermissionDenied("You cannot access another tenant's PDM data")
            responses = responses.filter(tenant=requested_tenant)
        if request.query_params.get("program"):
            program = scoped_program(request, request.query_params["program"])
            if requested_tenant and str(program.tenant_id) != str(requested_tenant):
                raise PermissionDenied("The selected program does not belong to the selected tenant")
            responses = responses.filter(program=program)
        return Response(list(responses.values("id", "tenant", "tenant__name", "program", "program__name", "channel", "location", "received_count", "received_rate", "amount_received", "access_problem_rate", "complaint_rate", "satisfaction", "created_at")))

    if request.user.role in {User.Role.AUDITOR, User.Role.SUPPORT}:
        raise PermissionDenied("Your PDM access is limited to summaries")
    if request.data.get("program") in {None, ""} or request.data.get("received_count") in {None, ""}:
        raise ValidationError({"program": "Required", "received_count": "Enter the number of people who received assistance."})
    try:
        received_count = int(request.data["received_count"])
    except (TypeError, ValueError):
        raise ValidationError({"received_count": "Enter a whole number."})
    if received_count < 1:
        raise ValidationError({"received_count": "Enter at least one recipient."})
    program = scoped_program(request, request.data["program"])
    existing_responses = PDMResponse.objects.filter(program=program)
    metrics = pdm_program_metrics(program, existing_responses)
    if not metrics["paid_beneficiaries"]:
        raise ValidationError({"received_count": "No successfully simulated payments are available for this program yet."})
    if received_count > metrics["remaining_recipients"]:
        raise ValidationError({"received_count": f"Only {metrics['remaining_recipients']} recipients remain to be recorded for this program."})
    try:
        access_problem_rate = Decimal(str(request.data.get("access_problem_rate") or 0))
        satisfaction = Decimal(str(request.data.get("satisfaction") or 0))
    except Exception:
        raise ValidationError("Access problem rate and satisfaction must be numbers.")
    if any(value < 0 or value > 100 for value in (access_problem_rate, satisfaction)):
        raise ValidationError("Access problem rate and satisfaction must be between 0 and 100.")
    channel = PaymentChannelConfig.objects.filter(program=program, is_active=True).order_by("provider_name").values_list("channel_type", flat=True).first() or "UNSPECIFIED"
    location = Household.objects.filter(program=program).order_by("registration_date").values_list("location", flat=True).first() or "UNSPECIFIED"
    received_rate = (Decimal(received_count) * Decimal("100") / Decimal(metrics["paid_beneficiaries"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amount_received = (metrics["distributed_amount"] * Decimal(received_count) / Decimal(metrics["paid_beneficiaries"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    response = PDMResponse.objects.create(tenant=program.tenant, program=program, channel=channel, location=location, received_count=received_count, received_rate=received_rate, amount_received=amount_received, access_problem_rate=access_problem_rate, complaint_rate=metrics["complaint_rate"], satisfaction=satisfaction, created_by=request.user)
    audit(request.user, "PDM_RESPONSE_CREATED", response, after={"program": str(program.id), "received_count": received_count}, tenant=program.tenant)
    return Response({"status": "created", "id": str(response.id), "submitted_at": response.created_at}, status=status.HTTP_201_CREATED)


# Returns readable PDM summary data for the selected tenant and program.
@api_view(["GET"])
def pdm_summary_view(request):
    if request.user.role not in {User.Role.ADMIN, User.Role.SUPPORT, User.Role.FIELD_OFFICER, User.Role.MANAGER, User.Role.AUDITOR}:
        raise PermissionDenied("Your role does not have permission for PDM summaries")
    responses = PDMResponse.objects.all() if request.user.is_superuser else PDMResponse.objects.filter(tenant=request.user.tenant)
    selected_tenant = None
    requested_tenant = request.query_params.get("tenant")
    if request.user.is_superuser and requested_tenant:
        selected_tenant = get_object_or_404(Tenant, id=requested_tenant)
        responses = responses.filter(tenant=selected_tenant)
    elif not request.user.is_superuser:
        if requested_tenant and requested_tenant != str(request.user.tenant_id):
            raise PermissionDenied("You cannot access another tenant's PDM data")
        selected_tenant = request.user.tenant
    selected_program = None
    for field in ("program", "channel", "location"):
        if request.query_params.get(field):
            if field == "program":
                selected_program = scoped_program(request, request.query_params[field])
                if selected_tenant and selected_program.tenant_id != selected_tenant.id:
                    raise PermissionDenied("The selected program does not belong to the selected tenant")
                responses = responses.filter(program=selected_program)
            else:
                responses = responses.filter(**{field: request.query_params[field]})
    summary_programs = Program.objects.all() if request.user.is_superuser else Program.objects.filter(tenant=request.user.tenant)
    if selected_tenant:
        summary_programs = summary_programs.filter(tenant=selected_tenant)
    if selected_program:
        summary_programs = summary_programs.filter(id=selected_program.id)
    successful_payments = PaymentInstruction.objects.filter(
        Q(cash_entitlement__program__in=summary_programs) | Q(enrollment__program__in=summary_programs),
        status=PaymentInstruction.Status.SUCCESS,
    )
    paid_beneficiaries = (
        successful_payments.filter(cash_entitlement__isnull=False)
        .values("cash_entitlement__household_id").distinct().count()
        + successful_payments.filter(cash_entitlement__isnull=True, beneficiary__isnull=False)
        .values("beneficiary_id").distinct().count()
    )
    distributed_amount = successful_payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    complaints = Complaint.objects.filter(beneficiary__household__program__in=summary_programs).count()
    totals = responses.aggregate(received_count=Sum("received_count"), amount_received=Sum("amount_received"), access_problem_rate=Sum("access_problem_rate"), satisfaction=Sum("satisfaction"), count=Count("id"))
    count = totals["count"] or 0
    recorded_recipients = totals["received_count"] or 0
    channels = list(responses.exclude(channel="").values_list("channel", flat=True).distinct().order_by("channel"))
    locations = list(responses.exclude(location="").values_list("location", flat=True).distinct().order_by("location"))
    eligible_households = HouseholdEligibility.objects.filter(program__in=summary_programs, status=HouseholdEligibility.Status.ELIGIBLE).values("household_id").distinct().count()
    enrolled_households = HouseholdEnrollment.objects.filter(program__in=summary_programs, status__in=[HouseholdEnrollment.Status.ACCEPTED, HouseholdEnrollment.Status.APPROVED]).values("household_id").distinct().count()
    approved = Enrollment.objects.filter(program__in=summary_programs, status=Enrollment.Status.APPROVED)
    cash_beneficiaries = approved.filter(assistance_modality__in=[Enrollment.AssistanceModality.CASH, Enrollment.AssistanceModality.CASH_NFI]).values("beneficiary_id").distinct().count()
    nfi_beneficiaries = approved.filter(assistance_modality__in=[Enrollment.AssistanceModality.NFI, Enrollment.AssistanceModality.CASH_NFI]).values("beneficiary_id").distinct().count()
    cash_nfi_beneficiaries = approved.filter(assistance_modality=Enrollment.AssistanceModality.CASH_NFI).values("beneficiary_id").distinct().count()
    nfi_entitlements = NFIEntitlement.objects.filter(program__in=summary_programs)
    nfi_issues = DistributionIssue.objects.filter(entitlement__in=nfi_entitlements)
    nfi_stock = NFIItem.objects.filter(program__in=summary_programs).aggregate(total=Sum("available_quantity"))["total"] or 0
    nfi_allocated = StockMovement.objects.filter(program__in=summary_programs, movement_type=StockMovement.MovementType.ALLOCATION).aggregate(total=Sum("quantity"))["total"] or 0
    warehouse_stock = list(NFIItem.objects.filter(program__in=summary_programs).values("warehouse_id", "warehouse__name", "warehouse__location").annotate(initial_quantity=Sum("initial_quantity"), available_quantity=Sum("available_quantity")).order_by("warehouse__name"))
    delivery_exceptions = nfi_issues.exclude(delivery_status=DistributionIssue.DeliveryStatus.RECEIVED).count()
    program_financials = [payment_budget_totals(program) for program in summary_programs]
    budget_amount = sum((item["budget_amount"] for item in program_financials), Decimal("0"))
    committed_amount = sum((item["committed"] for item in program_financials), Decimal("0"))
    successful_fees = sum((item["fees"] for item in program_financials), Decimal("0"))
    used_amount = sum((item["used"] for item in program_financials), Decimal("0"))
    remaining_amount = sum((item["remaining"] for item in program_financials), Decimal("0"))
    scoped_payments = PaymentInstruction.objects.filter(
        Q(cash_entitlement__program__in=summary_programs) | Q(enrollment__program__in=summary_programs)
    ).select_related("cash_entitlement__household", "cash_entitlement__program", "channel_config")
    payment_reviews = PaymentDeliveryReview.objects.filter(program__in=summary_programs)
    payment_transactions = [
        {
            "id": str(payment.id),
            "household_reference": household_reference(payment.cash_entitlement.household),
            "program_name": payment.cash_entitlement.program.name,
            "amount": str(payment.amount),
            "fee": str(payment.applied_fee),
            "total_impact": str(payment.amount + payment.applied_fee),
            "currency": payment.currency,
            "status": payment.status,
            "channel": payment.channel_config.provider_name,
        }
        for payment in scoped_payments
        if payment.cash_entitlement_id
    ]
    return Response({
        "program": {"id": str(selected_program.id), "name": selected_program.name} if selected_program else None,
        "tenant": {"id": str(selected_tenant.id), "name": selected_tenant.name} if selected_tenant else None,
        "channels": channels,
        "locations": locations,
        "paid_beneficiaries": paid_beneficiaries,
        "recorded_recipients": recorded_recipients,
        "remaining_recipients": max(paid_beneficiaries - recorded_recipients, 0),
        "distributed_amount": str(distributed_amount),
        "budget_amount": str(budget_amount),
        "total_payment_entitlements": CashEntitlement.objects.filter(program__in=summary_programs).count(),
        "committed_payment_amount": str(committed_amount),
        "successful_payment_funding": str(distributed_amount),
        "successful_fees": str(successful_fees),
        "budget_used": str(used_amount),
        "budget_remaining": str(remaining_amount),
        "successful_transactions": scoped_payments.filter(status=PaymentInstruction.Status.SUCCESS).count(),
        "failed_transactions": scoped_payments.filter(status=PaymentInstruction.Status.FAILED).count(),
        "pending_transactions": scoped_payments.filter(status__in=[PaymentInstruction.Status.CREATED, PaymentInstruction.Status.SUBMITTED]).count(),
        "payment_delivery_reviews": payment_reviews.count(),
        "payments_received": payment_reviews.filter(delivery_status=PaymentDeliveryReview.DeliveryStatus.RECEIVED).count(),
        "payments_partially_received": payment_reviews.filter(delivery_status=PaymentDeliveryReview.DeliveryStatus.PARTIALLY_RECEIVED).count(),
        "payments_not_received": payment_reviews.filter(delivery_status=PaymentDeliveryReview.DeliveryStatus.NOT_RECEIVED).count(),
        "payment_transactions": payment_transactions,
        "complaints": complaints,
        "received_rate": float(Decimal(recorded_recipients) * Decimal("100") / Decimal(paid_beneficiaries)) if paid_beneficiaries else 0,
        "amount_received": str(totals["amount_received"] or 0),
        "access_problem_rate": float(totals["access_problem_rate"] / count) if count else 0,
        "complaint_rate": float(Decimal(complaints) * Decimal("100") / Decimal(paid_beneficiaries)) if paid_beneficiaries else 0,
        "satisfaction": float(totals["satisfaction"] / count) if count else 0,
        "responses": count,
        "eligible_households": eligible_households,
        "enrolled_households": enrolled_households,
        "approved_beneficiaries": approved.count(),
        "cash_beneficiaries": cash_beneficiaries,
        "nfi_beneficiaries": nfi_beneficiaries,
        "cash_nfi_beneficiaries": cash_nfi_beneficiaries,
        "cash_entitlements": CashEntitlement.objects.filter(program__in=summary_programs).count(),
        "nfi_entitlements": nfi_entitlements.count(),
        "nfi_entitled_quantity": nfi_entitlements.aggregate(total=Sum("quantity"))["total"] or 0,
        "nfi_allocated_quantity": nfi_allocated,
        "nfi_distributed_quantity": nfi_issues.aggregate(total=Sum("actual_quantity"))["total"] or 0,
        "nfi_pending_delivery": nfi_entitlements.filter(issues__isnull=True).count(),
        "distribution_events": DistributionEvent.objects.filter(program__in=summary_programs).count(),
        "available_nfi_stock": nfi_stock,
        "nfi_enabled": summary_programs.filter(nfi_enabled=True).exists(),
        "warehouse_stock": warehouse_stock,
        "open_delivery_exceptions": delivery_exceptions,
        "delivery_status_counts": {status: nfi_issues.filter(delivery_status=status).count() for status, _label in DistributionIssue.DeliveryStatus.choices},
    })


@api_view(["POST"])
def registration_sync_view(request):
    if request.user.role not in {User.Role.ADMIN, User.Role.FIELD_OFFICER}:
        raise PermissionDenied("Only field officers and administrators can synchronize registrations")
    program_id = request.data.get("program_id")
    household_data = request.data.get("household", request.data)
    client_generated_id = household_data.get("client_generated_id")
    if not program_id or not client_generated_id:
        raise ValidationError({"program_id": "Required", "client_generated_id": "Required"})

    program = get_object_or_404(Program, id=program_id, tenant=request.user.tenant)
    household, household_created = Household.objects.get_or_create(
        tenant=program.tenant,
        registration_reference=client_generated_id,
        defaults={
            "program": program,
            "household_size": household_data.get("household_size", 1),
            "location": household_data.get("location", "Unspecified"),
            "registration_date": household_data.get("registration_date") or timezone.localdate(),
            "created_by": request.user,
        },
    )
    if household.program_id != program.id:
        raise ValidationError({"client_generated_id": "Already belongs to another program"})

    beneficiaries_created = 0
    for beneficiary_data in request.data.get("beneficiaries", []):
        raw_national_id = beneficiary_data.get("national_id") or beneficiary_data.get("number")
        full_name = beneficiary_data.get("full_name")
        if not raw_national_id or not full_name:
            raise ValidationError({"beneficiaries": "Each beneficiary requires national_id and full_name"})
        normalized_national_id = normalize_national_id(raw_national_id)
        phone_number = normalize_phone_number(beneficiary_data.get("phone_number") or beneficiary_data.get("phone_last4"))
        _, created = Beneficiary.objects.get_or_create(
            household=household,
            number=national_id_reference(normalized_national_id),
            defaults={
                "full_name": full_name,
                "gender": beneficiary_data.get("gender", ""),
                "phone_number": phone_number,
                "national_id_hash": national_id_digest(normalized_national_id),
                "consent_given": beneficiary_data.get("consent_given", False),
                "created_by": request.user,
            },
        )
        beneficiaries_created += int(created)
    audit(request.user, "OFFLINE_REGISTRATION_SYNCED", household, after={"client_generated_id": client_generated_id}, tenant=program.tenant)
    return Response({
        "status": "synchronized",
        "household_id": str(household.id),
        "household_created": household_created,
        "beneficiaries_created": beneficiaries_created,
        "client_generated_id": client_generated_id,
        "tenant_isolated": True,
    }, status=status.HTTP_201_CREATED if household_created else status.HTTP_200_OK)
