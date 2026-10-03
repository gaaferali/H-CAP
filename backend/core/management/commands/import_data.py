import csv

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from core.models import Beneficiary, Household, Program, Tenant, User
from core.serializers import national_id_digest, national_id_reference, normalize_national_id, normalize_phone_number
from core.services import AI_AUTOMATION_ENABLED, run_deduplication_check


class Command(BaseCommand):
    help = "Import beneficiary CSV records into an explicitly selected tenant and program."

    def add_arguments(self, parser):
        parser.add_argument("csv_file", help="Path to the CSV file to import")
        parser.add_argument("--tenant-id", required=True, help="Target tenant UUID")
        parser.add_argument("--program-id", required=True, help="Target program UUID")
        parser.add_argument("--user-email", required=True, help="Existing staff user recorded as the importer")

    def handle(self, *args, **options):
        try:
            tenant = Tenant.objects.get(pk=options["tenant_id"])
            program = Program.objects.get(pk=options["program_id"], tenant=tenant)
            user = User.objects.get(email=options["user_email"], tenant=tenant, is_active=True)
        except (Tenant.DoesNotExist, Program.DoesNotExist, User.DoesNotExist) as exc:
            raise CommandError("Tenant, program, and active importing user must exist in the same tenant.") from exc

        with open(options["csv_file"], encoding="utf-8-sig", newline="") as file_handle:
            rows = list(csv.DictReader(file_handle))

        required_columns = {"client_generated_id", "household_size", "location", "full_name"}
        columns = set(rows[0].keys()) if rows else set()
        missing_columns = sorted(required_columns - columns)
        if not ({"national_id", "beneficiary_number"} & columns):
            missing_columns.append("national_id (or beneficiary_number)")
        if missing_columns:
            raise CommandError(f"Missing required columns: {', '.join(missing_columns)}")

        normalized_rows = []
        errors = []
        for row_number, row in enumerate(rows, start=2):
            try:
                household_size = int(str(row.get("household_size") or ""))
            except ValueError:
                household_size = 0
            values = {
                "client_generated_id": str(row.get("client_generated_id") or "").strip(),
                "household_size": household_size,
                "location": str(row.get("location") or "").strip(),
                "beneficiary_number": str(row.get("national_id") or row.get("beneficiary_number") or "").strip(),
                "full_name": str(row.get("full_name") or "").strip(),
                "gender": str(row.get("gender") or "").strip(),
                "phone_number": str(row.get("phone_number") or row.get("phone_last4") or "").strip(),
                "consent_given": str(row.get("consent_given") or "").strip().lower() in {"1", "true", "yes", "y"},
            }
            if not all(values[field] for field in ("client_generated_id", "location", "beneficiary_number", "full_name")) or household_size < 1:
                errors.append(row_number)
            else:
                try:
                    national_id = normalize_national_id(values["beneficiary_number"])
                    phone_number = normalize_phone_number(values["phone_number"])
                except ValidationError:
                    errors.append(row_number)
                    continue
                values["number"] = national_id_reference(national_id)
                values["national_id_hash"] = national_id_digest(national_id)
                values["phone_number"] = phone_number
                values["phone_last4"] = phone_number[-4:] if phone_number else ""
                normalized_rows.append(values)
        if errors:
            raise CommandError(f"Import aborted. Invalid rows: {', '.join(map(str, errors))}")

        households_created = beneficiaries_created = duplicate_flags = 0
        with transaction.atomic():
            for row in normalized_rows:
                household, household_created = Household.objects.get_or_create(
                    tenant=tenant,
                    registration_reference=row["client_generated_id"],
                    defaults={
                        "program": program,
                        "household_size": row["household_size"],
                        "location": row["location"],
                        "registration_date": timezone.localdate(),
                        "created_by": user,
                    },
                )
                if household.program_id != program.id:
                    raise CommandError(f"Client reference {row['client_generated_id']} already belongs to another program.")
                beneficiary, beneficiary_created = Beneficiary.objects.update_or_create(
                    household=household,
                    number=row["number"],
                    defaults={
                        "full_name": row["full_name"],
                        "gender": row["gender"],
                        "phone_number": row["phone_number"],
                        "national_id_hash": row["national_id_hash"],
                        "consent_given": row["consent_given"],
                        "created_by": user,
                    },
                )
                households_created += int(household_created)
                beneficiaries_created += int(beneficiary_created)
                if AI_AUTOMATION_ENABLED:
                    duplicate_flags += int(run_deduplication_check(beneficiary)["matches"] > 0)

        self.stdout.write(self.style.SUCCESS(
            f"Imported {len(normalized_rows)} valid rows: {households_created} households, "
            f"{beneficiaries_created} beneficiaries, {duplicate_flags} advisory duplicate flags."
        ))
