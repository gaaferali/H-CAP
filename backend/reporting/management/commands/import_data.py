import csv

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError

from accounts.models import Tenant
from assistance.models import Beneficiary, Household
from assistance.serializer_helpers import (
    national_id_digest,
    national_id_reference,
    normalize_national_id,
    normalize_phone_number,
)
from programs.models import Program
from automation.services import run_deduplication_check


class Command(BaseCommand):
    help = "Import household and beneficiary registrations from a CSV file."

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str, help="Path to the CSV file.")
        parser.add_argument("--tenant-id", required=True, help="Tenant UUID.")
        parser.add_argument("--program-id", required=True, help="Program UUID.")
        parser.add_argument("--user-email", required=True, help="Active importing user's email.")

    @staticmethod
    def _normalize_header(value):
        return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")

    @staticmethod
    def _parse_consent(value):
        return str(value or "").strip().lower() in {"1", "true", "yes", "y"}

    def _read_rows(self, csv_file_path):
        try:
            with open(csv_file_path, mode="r", encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                if not reader.fieldnames:
                    raise CommandError("CSV file must include a header row.")
                headers = [self._normalize_header(header) for header in reader.fieldnames]
                rows = [
                    {
                        self._normalize_header(key): value
                        for key, value in row.items()
                        if key is not None
                    }
                    for row in reader
                ]
        except (OSError, UnicodeError, csv.Error) as exc:
            raise CommandError(f"Could not read CSV file: {exc}") from exc

        required = {"client_generated_id", "household_size", "location", "full_name"}
        missing = sorted(required - set(headers))
        if not ({"national_id", "beneficiary_number"} & set(headers)):
            missing.append("national_id (or beneficiary_number)")
        if missing:
            raise CommandError(f"Missing required columns: {', '.join(missing)}")
        return rows

    def _validate_rows(self, rows):
        normalized = []
        errors = []
        for row_number, row in enumerate(rows, start=2):
            client_id = str(row.get("client_generated_id") or "").strip()
            national_id = str(
                row.get("national_id") or row.get("beneficiary_number") or ""
            ).strip()
            full_name = str(row.get("full_name") or "").strip()
            location = str(row.get("location") or "").strip()
            if not client_id or not national_id or not full_name or not location:
                errors.append(
                    f"row {row_number}: client_generated_id, location, national_id "
                    "(or beneficiary_number), and full_name are required"
                )
                continue

            try:
                household_size = int(row.get("household_size"))
                if household_size < 1:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"row {row_number}: household_size must be a positive whole number")
                continue

            try:
                normalized_national_id = normalize_national_id(national_id)
                phone_number = normalize_phone_number(row.get("phone_number"))
            except ValidationError as exc:
                errors.append(f"row {row_number}: {str(exc.detail)}")
                continue

            verification_status = str(
                row.get("verification_status") or Beneficiary.VerificationStatus.PENDING
            ).strip().upper()
            if verification_status not in Beneficiary.VerificationStatus.values:
                errors.append(
                    f"row {row_number}: verification_status must be PENDING, VERIFIED, or REJECTED"
                )
                continue

            registration_date_value = str(row.get("registration_date") or "").strip()
            registration_date = parse_date(registration_date_value) if registration_date_value else timezone.localdate()
            if registration_date is None:
                errors.append(f"row {row_number}: registration_date must use YYYY-MM-DD")
                continue

            date_of_birth_value = str(row.get("date_of_birth") or "").strip()
            date_of_birth = parse_date(date_of_birth_value) if date_of_birth_value else None
            if date_of_birth_value and date_of_birth is None:
                errors.append(f"row {row_number}: date_of_birth must use YYYY-MM-DD")
                continue

            normalized.append(
                {
                    "row_number": row_number,
                    "client_id": client_id,
                    "household_size": household_size,
                    "location": location,
                    "registration_date": registration_date,
                    "number": national_id_reference(normalized_national_id),
                    "national_id_hash": str(row.get("national_id_hash") or "").strip()
                    or national_id_digest(normalized_national_id),
                    "full_name": full_name,
                    "gender": str(row.get("gender") or "").strip(),
                    "date_of_birth": date_of_birth,
                    "phone_number": phone_number,
                    "consent_given": self._parse_consent(row.get("consent_given")),
                    "verification_status": verification_status,
                }
            )

        grouped = {}
        for row in normalized:
            household_data = (
                row["household_size"],
                row["location"],
                row["registration_date"],
            )
            prior = grouped.setdefault(row["client_id"], household_data)
            if prior != household_data:
                errors.append(
                    f"row {row['row_number']}: household fields differ between rows "
                    f"for client_generated_id {row['client_id']}"
                )
        if errors:
            raise CommandError("Invalid CSV rows:\n" + "\n".join(errors))
        return normalized

    @transaction.atomic
    def _import_rows(self, rows, tenant, program, importing_user):
        households_created = 0
        beneficiaries_created = 0
        duplicates_flagged = 0

        for row in rows:
            existing_household = Household.objects.select_for_update().filter(
                registration_reference=row["client_id"]
            ).first()
            if existing_household and existing_household.tenant_id != tenant.id:
                raise CommandError(
                    f"row {row['row_number']}: client_generated_id "
                    f"{row['client_id']} belongs to another tenant"
                )
            if existing_household and existing_household.program_id != program.id:
                raise CommandError(
                    f"row {row['row_number']}: client_generated_id "
                    f"{row['client_id']} belongs to another program"
                )

            household, created = Household.objects.get_or_create(
                tenant=tenant,
                registration_reference=row["client_id"],
                defaults={
                    "program": program,
                    "household_size": row["household_size"],
                    "location": row["location"],
                    "registration_date": row["registration_date"],
                    "created_by": importing_user,
                },
            )
            if household.program_id != program.id or household.tenant_id != tenant.id:
                raise CommandError(
                    f"row {row['row_number']}: existing household is outside the selected program or tenant"
                )
            household = Household.objects.select_for_update().get(pk=household.pk)
            beneficiary = Beneficiary.objects.filter(
                household=household,
                number=row["number"],
            ).first()
            if beneficiary is None and household.beneficiaries.count() >= household.household_size:
                raise CommandError(
                    f"row {row['row_number']}: household {row['client_id']} already "
                    f"has its maximum of {household.household_size} beneficiaries"
                )

            defaults = {
                "full_name": row["full_name"],
                "gender": row["gender"],
                "date_of_birth": row["date_of_birth"],
                "phone_number": row["phone_number"],
                "national_id_hash": row["national_id_hash"],
                "consent_given": row["consent_given"],
                "verification_status": row["verification_status"],
                "created_by": importing_user,
            }
            beneficiary, beneficiary_created = Beneficiary.objects.update_or_create(
                household=household,
                number=row["number"],
                defaults=defaults,
            )
            households_created += int(created)
            beneficiaries_created += int(beneficiary_created)
            duplicates_flagged += int(
                run_deduplication_check(beneficiary)["matches"] > 0
            )

        return households_created, beneficiaries_created, duplicates_flagged

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(pk=options["tenant_id"]).first()
        if not tenant:
            raise CommandError(f"Tenant {options['tenant_id']} was not found.")

        program = Program.objects.filter(pk=options["program_id"]).first()
        if not program:
            raise CommandError(f"Program {options['program_id']} was not found.")
        if program.tenant_id != tenant.id:
            raise CommandError("The selected program does not belong to the selected tenant.")

        user_model = get_user_model()
        importing_user = user_model.objects.filter(
            email=options["user_email"],
            is_active=True,
        ).first()
        if not importing_user:
            raise CommandError(
                f"Active importing user {options['user_email']} was not found."
            )
        if importing_user.tenant_id != tenant.id:
            raise CommandError("The importing user does not belong to the selected tenant.")

        rows = self._validate_rows(self._read_rows(options["csv_file"]))
        if not rows:
            raise CommandError("CSV file has no beneficiary rows to import.")

        households_created, beneficiaries_created, duplicates_flagged = self._import_rows(
            rows,
            tenant,
            program,
            importing_user,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {len(rows)} rows; created {households_created} households "
                f"and {beneficiaries_created} beneficiaries; flagged "
                f"{duplicates_flagged} possible duplicate rows."
            )
        )
