from decimal import Decimal
from difflib import SequenceMatcher

from django.db import transaction

from core.models import AISignal, Beneficiary, ReviewTask


class DuplicateDetector:
    """Explainable, non-destructive probabilistic duplicate detector."""

    MODEL_VERSION = "dedup-v2"
    RULE_VERSION = "rules-v2"
    HIGH_THRESHOLD = 0.85
    MEDIUM_THRESHOLD = 0.70

    @staticmethod
    def normalize(value):
        return " ".join(str(value or "").lower().strip().split())

    @classmethod
    def name_similarity(cls, name1, name2):
        a, b = cls.normalize(name1), cls.normalize(name2)
        return SequenceMatcher(None, a, b).ratio() if a and b else 0.0

    @classmethod
    def compare(cls, beneficiary, candidate):
        factors = []
        scores = []

        name_score = cls.name_similarity(beneficiary.full_name, candidate.full_name)
        if name_score >= 0.80:
            factors.append({"field": "full_name", "match": round(name_score, 4)})
            scores.append(("name", name_score))

        if beneficiary.phone_last4 and candidate.phone_last4 and beneficiary.phone_last4 == candidate.phone_last4:
            factors.append({"field": "phone_last4", "match": 1.0})
            scores.append(("phone", 1.0))

        if beneficiary.national_id_hash and candidate.national_id_hash and beneficiary.national_id_hash == candidate.national_id_hash:
            factors.append({"field": "national_id_hash", "match": 1.0})
            scores.append(("national_id", 1.0))

        if beneficiary.date_of_birth and candidate.date_of_birth and beneficiary.date_of_birth == candidate.date_of_birth:
            factors.append({"field": "date_of_birth", "match": 1.0})
            scores.append(("dob", 1.0))

        if beneficiary.gender and candidate.gender and cls.normalize(beneficiary.gender) == cls.normalize(candidate.gender):
            factors.append({"field": "gender", "match": 1.0})
            scores.append(("gender", 1.0))

        if beneficiary.household.location and candidate.household.location and cls.normalize(beneficiary.household.location) == cls.normalize(candidate.household.location):
            factors.append({"field": "location", "match": 1.0})
            scores.append(("location", 1.0))

        if not scores:
            return 0.0, factors

        weights = {"name": 0.35, "phone": 0.25, "national_id": 0.25, "dob": 0.08, "gender": 0.03, "location": 0.04}
        weighted = sum(score * weights.get(field, 0) for field, score in scores)
        total = sum(weights.get(field, 0) for field, _ in scores)
        return round(weighted / total, 4) if total else 0.0, factors

    @classmethod
    @transaction.atomic
    def detect_for(cls, beneficiary_id):
        beneficiary = Beneficiary.objects.select_related("household", "household__program", "household__tenant").get(id=beneficiary_id)
        queryset = Beneficiary.objects.select_related("household").filter(
            household__tenant=beneficiary.household.tenant,
            household__program=beneficiary.household.program,
        ).exclude(id=beneficiary.id)

        results = []
        for candidate in queryset.iterator():
            score, factors = cls.compare(beneficiary, candidate)
            if score < cls.MEDIUM_THRESHOLD:
                continue

            priority = ReviewTask.Priority.HIGH if score >= cls.HIGH_THRESHOLD else ReviewTask.Priority.MEDIUM
            reason = f"Possible duplicate: {beneficiary.full_name} matches {candidate.full_name}."
            evidence = {"candidate_id": str(candidate.id), "candidate_name": candidate.full_name, "score": score, "matching_factors": factors}

            # Candidate ID is part of evidence, so repeated scans do not create a task/signal storm.
            signal = AISignal.objects.filter(
                tenant=beneficiary.household.tenant,
                entity_type="BENEFICIARY",
                entity_id=str(beneficiary.id),
                signal_type="POSSIBLE_DUPLICATE",
                model_version=cls.MODEL_VERSION,
                status=AISignal.Status.OPEN,
                evidence__candidate_id=str(candidate.id),
            ).first()
            if signal is None:
                signal = AISignal.objects.create(
                    tenant=beneficiary.household.tenant,
                    program=beneficiary.household.program,
                    entity_type="BENEFICIARY",
                    entity_id=str(beneficiary.id),
                    signal_type="POSSIBLE_DUPLICATE",
                    score=Decimal(str(score)),
                    confidence=Decimal(str(score)),
                    reason=reason,
                    evidence=evidence,
                    model_version=cls.MODEL_VERSION,
                    rule_version=cls.RULE_VERSION,
                    status=AISignal.Status.OPEN,
                )

            task, _ = ReviewTask.objects.get_or_create(
                tenant=beneficiary.household.tenant,
                program=beneficiary.household.program,
                task_type=ReviewTask.TaskType.DUPLICATE_REVIEW,
                entity_type="BENEFICIARY",
                entity_id=str(beneficiary.id),
                status=ReviewTask.Status.OPEN,
                resolution__icontains=str(candidate.id),
                defaults={
                    "priority": priority,
                    "resolution": f"Review possible duplicate candidate {candidate.id}; signal {signal.id}.",
                },
            )
            results.append({"candidate_id": str(candidate.id), "candidate_name": candidate.full_name, "score": score, "confidence": score, "priority": priority, "matching_factors": factors, "signal_id": str(signal.id), "review_task_id": str(task.id)})
        return results
