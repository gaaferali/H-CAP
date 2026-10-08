import re

from django.db.models import Q
from django.utils.crypto import salted_hmac
from rest_framework import serializers

from .models import Enrollment, HouseholdEnrollment


def normalize_phone_number(value):
    normalized = re.sub(r"[\s-]+", "", str(value or "").strip())
    if not normalized:
        return ""
    if not re.fullmatch(r"\+?[0-9]{6,29}", normalized):
        raise serializers.ValidationError("Use a valid international phone number with digits, spaces, hyphens, and an optional leading +.")
    return normalized


def normalize_national_id(value):
    normalized = re.sub(r"[\s-]+", "", str(value or "").strip()).upper()
    if not normalized:
        raise serializers.ValidationError("National ID is required.")
    if not re.fullmatch(r"[A-Z0-9]{3,80}", normalized):
        raise serializers.ValidationError("National ID must contain letters or numbers and may include spaces or hyphens.")
    return normalized


def national_id_digest(national_id):
    return salted_hmac("hcap.beneficiary.national-id", national_id).hexdigest()


def safe_beneficiary_reference(beneficiary):
    """Use the same privacy-preserving display rule as BeneficiarySerializer."""
    return f"•••• {beneficiary.number[-4:]}" if beneficiary.number else "Not recorded"


def national_id_reference(national_id):
    return f"NID-{national_id_digest(national_id).upper()}"


def household_reference(household):
    if not household:
        return "Not recorded"
    return household.registration_reference or f"HH-{str(household.id).split('-')[0].upper()}"


def household_member_count(household):
    return household.beneficiaries.count() if household else 0


def household_member_details(household):
    if not household:
        return []
    return list(household.beneficiaries.values("id", "full_name", "status"))


def approved_household_modality(household, program, modality, include_legacy=True):
    if not household or household.program_id != program.id:
        return False
    if HouseholdEnrollment.objects.filter(
        household=household,
        program=program,
        status__in=[HouseholdEnrollment.Status.ACCEPTED, HouseholdEnrollment.Status.APPROVED],
        assistance_modality__in=modality,
    ).exists():
        return True
    if not include_legacy:
        return False
    return Enrollment.objects.filter(
        Q(household=household) | Q(household__isnull=True, beneficiary__household=household),
        program=program,
        status=Enrollment.Status.APPROVED,
        assistance_modality__in=modality,
    ).exists()
