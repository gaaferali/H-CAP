from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from .models import (
    AISignal,
    Complaint,
    ComplaintAIAnalysis,
    PaymentEvent,
    PaymentInstruction,
    ReviewTask,
)

MODEL_VERSION = "complaint-rules-v1"

CATEGORY_RULES = {
    "PAYMENT_NOT_RECEIVED": ["not received", "did not receive", "didn't receive", "didnt receive", "money not", "payment not", "ما وصل", "ما استلم", "ما استلمت", "المبلغ ما", "الدفع ما"],
    "PAYMENT_DELAY": ["delay", "delayed", "late", "waiting", "متأخر", "تأخير", "لسه", "لم يصل"],
    "WRONG_AMOUNT": ["wrong amount", "incorrect amount", "less than", "missing amount", "مبلغ خطأ", "المبلغ ناقص", "ناقص"],
    "REGISTRATION": ["registration", "register", "registered", "تسجيل", "التسجيل", "سجل", "تسجيلنا"],
    "ELIGIBILITY": ["eligible", "eligibility", "rejected", "not eligible", "استحقاق", "مستحق", "رفض", "مرفوض", "غير مستحق"],
    "AGENT_BEHAVIOR": ["agent", "staff", "field officer", "موظف", "عامل", "مندوب", "سلوك"],
    "ACCESS": ["access", "cannot access", "can't access", "صعوبة", "ما قادر", "لا استطيع", "الوصول"],
    "OTHER": [],
}

CRITICAL_TERMS = ["threat", "violence", "abuse", "harassment", "safety", "خطر", "عنف", "إساءة", "تحرش", "تهديد"]
HIGH_TERMS = ["urgent", "emergency", "critical", "خطر", "طارئ", "عاجل"]


def _text(complaint):
    return f"{complaint.category or ''} {complaint.description or ''}".strip().lower()


def _contains(text, terms):
    return [term for term in terms if term in text]


def _classify(text):
    scores = {}
    for category, terms in CATEGORY_RULES.items():
        if not terms:
            continue
        hits = _contains(text, terms)
        if hits:
            scores[category] = len(hits)
    if not scores:
        return "OTHER", []
    category = max(scores, key=scores.get)
    return category, scores


def _payment_context(complaint):
    instruction = complaint.instruction
    if not instruction:
        return {}
    events = list(PaymentEvent.objects.filter(instruction=instruction).order_by("-created_at")[:5])
    return {
        "payment_instruction_id": str(instruction.id),
        "payment_status": instruction.status,
        "payment_amount": str(instruction.amount),
        "currency": instruction.currency,
        "provider_reference": instruction.provider_reference or "",
        "recent_events": [
            {
                "event_type": e.event_type,
                "provider_status": e.provider_status,
                "provider_transaction_id": e.provider_transaction_id,
                "from_status": e.from_status,
                "to_status": e.to_status,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
    }


def _analyze(complaint):
    text = _text(complaint)
    category, category_scores = _classify(text)
    critical_hits = _contains(text, CRITICAL_TERMS)
    high_hits = _contains(text, HIGH_TERMS)
    payment = _payment_context(complaint)

    causes = []
    actions = []
    evidence = {
        "complaint_category_input": complaint.category,
        "keyword_matches": category_scores,
        "payment_context": payment,
    }

    if category == "PAYMENT_NOT_RECEIVED":
        if payment.get("payment_status") == PaymentInstruction.Status.SUCCESS:
            causes.append({
                "cause": "Payment is marked successful but the beneficiary reports non-receipt; provider status or delivery confirmation may need verification.",
                "confidence": 0.88,
            })
            actions.extend([
                "Verify the provider transaction reference.",
                "Review the payment event history and provider status.",
                "Check the reconciliation queue for an unresolved mismatch.",
            ])
        elif payment:
            causes.append({
                "cause": "The linked payment is not recorded as successful.",
                "confidence": 0.82,
            })
            actions.extend([
                "Review the current payment status and latest provider event.",
                "Retry or escalate according to the payment exception workflow.",
            ])
        else:
            causes.append({
                "cause": "No linked payment instruction was available for verification.",
                "confidence": 0.74,
            })
            actions.append("Link or verify the beneficiary payment record before closing the complaint.")

    elif category == "PAYMENT_DELAY":
        causes.append({"cause": "The complaint indicates a delayed payment or pending delivery.", "confidence": 0.76})
        actions.extend(["Check payment instruction status and latest provider event.", "Review unresolved payment/reconciliation exceptions."])

    elif category == "WRONG_AMOUNT":
        causes.append({"cause": "The reported amount may differ from the approved/payment instruction amount.", "confidence": 0.79})
        actions.extend(["Compare approved, instructed and actual paid amounts.", "Review reconciliation results and provider transaction details."])

    elif category == "ELIGIBILITY":
        causes.append({"cause": "The complaint concerns eligibility or an enrollment decision and requires verification against program rules.", "confidence": 0.75})
        actions.extend(["Review the eligibility evidence and program rules.", "Do not change eligibility automatically; obtain an authorized human decision."])

    elif category == "REGISTRATION":
        causes.append({"cause": "The complaint appears related to beneficiary or household registration data/process.", "confidence": 0.73})
        actions.extend(["Verify the beneficiary record and registration evidence.", "Check for duplicate or data-quality flags before editing source data."])

    elif category == "AGENT_BEHAVIOR":
        causes.append({"cause": "The complaint references staff/field-agent conduct and should be reviewed by an authorized human.", "confidence": 0.72})
        actions.extend(["Review the complaint details and audit trail.", "Escalate according to safeguarding/support procedures if required."])

    elif category == "ACCESS":
        causes.append({"cause": "The beneficiary reports an access or service-delivery problem.", "confidence": 0.70})
        actions.append("Review location/channel constraints and route to the responsible support team.")

    else:
        causes.append({"cause": "The complaint did not match a specific supported complaint pattern.", "confidence": 0.55})
        actions.append("Manual review is recommended to determine the appropriate category and next action.")

    if critical_hits:
        severity = Complaint.Severity.CRITICAL
    elif high_hits or category in {"PAYMENT_NOT_RECEIVED", "AGENT_BEHAVIOR"}:
        severity = Complaint.Severity.HIGH
    elif category in {"PAYMENT_DELAY", "WRONG_AMOUNT", "ELIGIBILITY"}:
        severity = Complaint.Severity.MEDIUM
    else:
        severity = complaint.severity or Complaint.Severity.MEDIUM

    confidence = max([Decimal(str(c["confidence"])) for c in causes] or [Decimal("0.50")])
    requires_escalation = severity in {Complaint.Severity.HIGH, Complaint.Severity.CRITICAL}

    summary = f"Complaint classified as {category.replace('_', ' ').title()}. " + causes[0]["cause"]
    if critical_hits:
        summary += " Safety-related language was detected and requires prompt human review."

    return {
        "category": category,
        "severity": severity,
        "summary": summary,
        "possible_causes": causes,
        "recommended_actions": actions,
        "evidence": evidence,
        "confidence": confidence,
        "requires_escalation": requires_escalation,
    }


@transaction.atomic
def analyze_complaint(complaint, actor=None):
    result = _analyze(complaint)
    analysis, _ = ComplaintAIAnalysis.objects.update_or_create(
        complaint=complaint,
        tenant=complaint.beneficiary.household.tenant,
        defaults={
            **result,
            "model_version": MODEL_VERSION,
        },
    )

    # AI assists; it does not resolve the complaint or modify source data.
    if result["requires_escalation"]:
        ReviewTask.objects.get_or_create(
            tenant=complaint.beneficiary.household.tenant,
            program=complaint.beneficiary.household.program,
            task_type=ReviewTask.TaskType.COMPLAINT_ESCALATION,
            entity_type="Complaint",
            entity_id=str(complaint.id),
            status=ReviewTask.Status.OPEN,
            defaults={
                "priority": ReviewTask.Priority.CRITICAL if result["severity"] == Complaint.Severity.CRITICAL else ReviewTask.Priority.HIGH,
            },
        )

    signal = AISignal.objects.filter(
        tenant=complaint.beneficiary.household.tenant,
        entity_type="Complaint",
        entity_id=str(complaint.id),
        signal_type="COMPLAINT_INTELLIGENCE",
        model_version=MODEL_VERSION,
        status=AISignal.Status.OPEN,
    ).first()
    if not signal:
        AISignal.objects.create(
            tenant=complaint.beneficiary.household.tenant,
            program=complaint.beneficiary.household.program,
            entity_type="Complaint",
            entity_id=str(complaint.id),
            signal_type="COMPLAINT_INTELLIGENCE",
            score=Decimal("0.8000") if result["requires_escalation"] else Decimal("0.5000"),
            confidence=result["confidence"],
            reason=result["summary"],
            evidence=result["evidence"],
            model_version=MODEL_VERSION,
            rule_version=MODEL_VERSION,
            status=AISignal.Status.OPEN,
        )
    return analysis
