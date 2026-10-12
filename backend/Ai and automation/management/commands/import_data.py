import csv
import uuid
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.contrib.auth import get_user_model

from core.models import (
    AuditEvent,
    Beneficiary,
    Enrollment,
    Household,
    Program,
    ReviewTask,
    Tenant,
)
from core.services import calculate_cash_transfer_amount, run_deduplication_check


class Command(BaseCommand):
    help = "Import synthetic CASH requests, derive eligibility/amounts, and create review/audit evidence."

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str)
        parser.add_argument("--tenant-id", type=str, default="")
        parser.add_argument("--program-id", type=str, default="")
        parser.add_argument("--user-email", type=str, default="")

    def handle(self, *args, **options):
        User = get_user_model()
        tenant = Tenant.objects.filter(pk=options["tenant_id"]).first() if options["tenant_id"] else Tenant.objects.first()
        program = Program.objects.filter(pk=options["program_id"]).first() if options["program_id"] else Program.objects.filter(tenant=tenant).first()
        actor = User.objects.filter(email=options["user_email"], tenant=tenant).first() if options["user_email"] else User.objects.filter(tenant=tenant).first()

        if not tenant or not program or not actor:
            raise CommandError("Tenant, Program and User must exist. Use --tenant-id/--program-id/--user-email when multiple records exist.")

        if program.tenant_id != tenant.id:
            raise CommandError("Program does not belong to the selected tenant.")

        imported = 0
        eligible = 0
        holds = 0
        duplicates = 0

        try:
            with open(options["csv_file"], newline="", encoding="utf-8-sig") as file:
                reader = csv.DictReader(file)
                required = {"row_id", "household_id", "size", "site", "consent", "status"}
                missing = required - set(reader.fieldnames or [])
                if missing:
                    raise CommandError(f"CSV is missing required columns: {', '.join(sorted(missing))}")

                seen_households = set()
                for row in reader:
                    row_id = (row.get("row_id") or "").strip()
                    household_id = (row.get("household_id") or "").strip()
                    full_name = (row.get("full_name") or f"Household {household_id} representative").strip()
                    site = (row.get("site") or "").strip().upper()
                    consent = (row.get("consent") or "").strip().upper()
                    status = (row.get("status") or "").strip().upper()

                    try:
                        size = int(row.get("size", ""))
                    except (TypeError, ValueError):
                        size = 0

                    if not row_id or not household_id:
                        raise CommandError("Every CASH row must have row_id and household_id.")

                    # Duplicate household IDs are held for human review; never silently
                    # merge two source rows or create a second cash entitlement.
                    if household_id in seen_households or Household.objects.filter(
                        tenant=tenant, client_generated_id=household_id
                    ).exists():
                        duplicates += 1
                        holds += 1
                        ReviewTask.objects.get_or_create(
                            tenant=tenant,
                            program=program,
                            task_type=ReviewTask.TaskType.DUPLICATE_REVIEW,
                            entity_type="IMPORT_ROW",
                            entity_id=row_id,
                            status=ReviewTask.Status.OPEN,
                            defaults={
                                "priority": ReviewTask.Priority.HIGH,
                                "resolution": f"CASH row {row_id} uses duplicate household ID {household_id}.",
                            },
                        )
                        AuditEvent.objects.create(
                            tenant=tenant,
                            actor=actor,
                            action="CASH_IMPORT_DUPLICATE_HELD",
                            entity_type="IMPORT_ROW",
                            entity_id=row_id,
                            after={"household_id": household_id, "reason": "DUPLICATE_REVIEW"},
                        )
                        continue

                    seen_households.add(household_id)

                    with transaction.atomic():
                        household = Household.objects.create(
                            tenant=tenant,
                            program=program,
                            household_size=max(size, 0),
                            location=site,
                            registration_date=date.today(),
                            client_generated_id=household_id,
                            created_by=actor,
                        )
                        beneficiary = Beneficiary.objects.create(
                            household=household,
                            number=row.get("beneficiary_number") or str(uuid.uuid4())[:8],
                            full_name=full_name,
                            gender=(row.get("gender") or "").strip(),
                            phone_last4=(row.get("phone_last4") or "").strip(),
                            national_id_hash=(row.get("national_id_hash") or "").strip(),
                            consent_given=consent == "YES",
                            verification_status=Beneficiary.VerificationStatus.VERIFIED if consent == "YES" else Beneficiary.VerificationStatus.PENDING,
                            status=Beneficiary.Status.ACTIVE,
                            created_by=actor,
                        )

                        reasons = []
                        if consent != "YES":
                            reasons.append("CONSENT_NO")
                        if size <= 0:
                            reasons.append("CORRECTION")
                        if site != "A":
                            reasons.append("SITE")
                        if status != "APPROVED":
                            reasons.append("STATUS_PENDING" if status == "PENDING" else "NOT_APPROVED")

                        if reasons:
                            holds += 1
                            enrollment = Enrollment.objects.create(
                                beneficiary=beneficiary,
                                program=program,
                                eligibility_status=Enrollment.EligibilityStatus.ON_HOLD,
                                status=Enrollment.Status.ENROLLED,
                            )
                            ReviewTask.objects.create(
                                tenant=tenant,
                                program=program,
                                task_type=ReviewTask.TaskType.ELIGIBILITY_REVIEW,
                                entity_type="ENROLLMENT",
                                entity_id=str(enrollment.id),
                                priority=ReviewTask.Priority.HIGH,
                                resolution=f"CASH row {row_id} held: {', '.join(reasons)}.",
                            )
                            audit_after = {
                                "row_id": row_id,
                                "household_id": household_id,
                                "outcome": "HOLD",
                                "reasons": reasons,
                            }
                        else:
                            eligible += 1
                            amount = calculate_cash_transfer_amount(size)
                            enrollment = Enrollment.objects.create(
                                beneficiary=beneficiary,
                                program=program,
                                eligibility_status=Enrollment.EligibilityStatus.ELIGIBLE,
                                status=Enrollment.Status.APPROVED,
                                approved_by=actor,
                            )
                            enrollment.approved_at = enrollment.enrolled_at
                            enrollment.save(update_fields=["approved_at"])
                            audit_after = {
                                "row_id": row_id,
                                "household_id": household_id,
                                "outcome": "APPROVED",
                                "amount": str(amount),
                            }

                        imported += 1
                        AuditEvent.objects.create(
                            tenant=tenant,
                            actor=actor,
                            action="CASH_IMPORT_ROW_PROCESSED",
                            entity_type="IMPORT_ROW",
                            entity_id=row_id,
                            after=audit_after,
                        )

                        # AI duplicate detection runs after source data is persisted;
                        # it only creates review evidence and never changes the source.
                        run_deduplication_check(beneficiary)

            self.stdout.write(self.style.SUCCESS(
                f"Imported={imported} Eligible={eligible} Holds={holds} Duplicates={duplicates}"
            ))
        except OSError as exc:
            raise CommandError(f"Could not read CSV: {exc}") from exc
