from decimal import Decimal
from difflib import SequenceMatcher

from django.db import transaction

from core.models import Beneficiary, AISignal, ReviewTask


class DuplicateDetector:
    """
    Detects possible duplicate beneficiaries.

    This is an advisory engine:
    - It NEVER deletes a beneficiary.
    - It NEVER changes eligibility.
    - It NEVER approves/rejects a beneficiary.
    - It creates an AI signal and a human review task.
    """

    MODEL_VERSION = "dedup-v1"
    RULE_VERSION = "rules-v1"

    # Thresholds
    HIGH_THRESHOLD = 0.85
    MEDIUM_THRESHOLD = 0.70

    @staticmethod
    def normalize(value):
        if not value:
            return ""

        return " ".join(str(value).lower().strip().split())

    @classmethod
    def name_similarity(cls, name1, name2):
        name1 = cls.normalize(name1)
        name2 = cls.normalize(name2)

        if not name1 or not name2:
            return 0.0

        return SequenceMatcher(None, name1, name2).ratio()

    @classmethod
    def compare(cls, beneficiary, candidate):
        """
        Compare two beneficiaries and return:
        - score
        - matching factors
        """

        factors = []
        scores = []

        # 1. Full name
        name_score = cls.name_similarity(
            beneficiary.full_name,
            candidate.full_name,
        )

        if name_score >= 0.80:
            factors.append(
                f"name_similarity={round(name_score, 3)}"
            )
            scores.append(("name", name_score))

        # 2. Phone last 4 digits
        if (
            beneficiary.phone_last4
            and candidate.phone_last4
            and beneficiary.phone_last4 == candidate.phone_last4
        ):
            factors.append("same_phone_last4")
            scores.append(("phone", 1.0))

        # 3. National ID hash
        if (
            beneficiary.national_id_hash
            and candidate.national_id_hash
            and beneficiary.national_id_hash
            == candidate.national_id_hash
        ):
            factors.append("same_national_id_hash")
            scores.append(("national_id", 1.0))

        # 4. Household
        if (
            beneficiary.household_id
            and candidate.household_id
            and beneficiary.household_id == candidate.household_id
        ):
            factors.append("same_household")
            scores.append(("household", 1.0))

        # 5. Location
        beneficiary_location = getattr(
            beneficiary.household,
            "location",
            None,
        )

        candidate_location = getattr(
            candidate.household,
            "location",
            None,
        )

        if (
            beneficiary_location
            and candidate_location
            and cls.normalize(beneficiary_location)
            == cls.normalize(candidate_location)
        ):
            factors.append("same_location")
            scores.append(("location", 1.0))

        # No useful matching signal
        if not scores:
            return 0.0, factors

        # Weighted scoring
        weights = {
            "name": 0.40,
            "phone": 0.25,
            "national_id": 0.25,
            "household": 0.05,
            "location": 0.05,
        }

        weighted_score = 0.0
        total_weight = 0.0

        for factor, score in scores:
            weight = weights.get(factor, 0.0)
            weighted_score += score * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0, factors

        final_score = weighted_score / total_weight

        return round(final_score, 4), factors

    @classmethod
    @transaction.atomic
    def detect_for(cls, beneficiary_id):
        """
        Find possible duplicates for a beneficiary.

        Returns a list of created signals/tasks.
        """

        beneficiary = (
            Beneficiary.objects
            .select_related("household")
            .get(id=beneficiary_id)
        )

        # Only compare within the same program/tenant context.
        queryset = (
            Beneficiary.objects
            .select_related("household")
            .filter(
                household__tenant=beneficiary.household.tenant,
                household__program=beneficiary.household.program,
            )
            .exclude(id=beneficiary.id)
        )

        results = []

        for candidate in queryset.iterator():
            score, factors = cls.compare(
                beneficiary,
                candidate,
            )

            if score < cls.MEDIUM_THRESHOLD:
                continue

            if score >= cls.HIGH_THRESHOLD:
                confidence = score
                priority = "HIGH"
            else:
                confidence = score
                priority = "MEDIUM"

            reason = (
                f"Possible duplicate detected between "
                f"{beneficiary.full_name} and "
                f"{candidate.full_name}."
            )

            evidence = {
                "candidate_id": str(candidate.id),
                "candidate_name": candidate.full_name,
                "score": score,
                "matching_factors": factors,
            }

            signal = AISignal.objects.create(
                tenant=beneficiary.household.tenant,
                program=beneficiary.household.program,
                entity_type="BENEFICIARY",
                entity_id=str(beneficiary.id),
                signal_type="POSSIBLE_DUPLICATE",
                score=Decimal(str(score)),
                confidence=Decimal(str(confidence)),
                reason=reason,
                evidence=evidence,
                model_version=cls.MODEL_VERSION,
                rule_version=cls.RULE_VERSION,
                status="OPEN",
            )

            task = ReviewTask.objects.create(
                tenant=beneficiary.household.tenant,
                program=beneficiary.household.program,
                task_type="DUPLICATE_REVIEW",
                entity_type="BENEFICIARY",
                entity_id=str(beneficiary.id),
                priority=priority,
                status="OPEN",
            )

            results.append(
                {
                    "candidate_id": str(candidate.id),
                    "candidate_name": candidate.full_name,
                    "score": score,
                    "confidence": confidence,
                    "priority": priority,
                    "matching_factors": factors,
                    "signal_id": str(signal.id),
                    "review_task_id": str(task.id),
                }
            )

        return results