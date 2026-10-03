from decimal import Decimal, InvalidOperation
from hashlib import sha256
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from .models import (
    AISignal,
    AutomationExecution,
    AutomationRule,
    Beneficiary,
    PaymentBatch,
    PaymentInstruction,
    ReconciliationItem,
    ReviewTask,
    SyncOperation,
    AuditEvent,
)




# ---------------------------------------------------------------------------
# Task 3 technical-control helpers (Amjad / Technical Lead)
# ---------------------------------------------------------------------------

TASK3_CASH_MAX = Decimal("160.00")
TASK3_CASH_BASE = Decimal("100.00")
TASK3_CASH_INCREMENT = Decimal("20.00")
TASK3_CASH_FEE = Decimal("2.00")


def calculate_cash_transfer_amount(household_size):
    """Return the Task 3 CASH-R1 transfer amount from the household size."""
    try:
        size = int(household_size)
    except (TypeError, ValueError):
        raise ValueError("household_size must be an integer")
    if size <= 0:
        raise ValueError("household_size must be positive")
    amount = TASK3_CASH_BASE + max(size - 4, 0) * TASK3_CASH_INCREMENT
    return min(amount, TASK3_CASH_MAX)


def is_task3_cash_program(program):
    """Detect the synthetic CASH-R1 program without forcing a schema migration."""
    config = getattr(program, "workflow_config", {}) or {}
    module = str(config.get("module", "")).upper()
    return module == "CASH" or "CASH-R1" in str(getattr(program, "name", "")).upper()


@transaction.atomic
def create_anomaly_review(*, tenant, program, entity_type, entity_id, reason, evidence, score="1.0000", confidence="0.9900"):
    """Create an explainable anomaly signal and one human-review task, idempotently."""
    signal, _ = _open_signal(
        tenant=tenant, program=program, entity_type=entity_type, entity_id=entity_id,
        signal_type="TASK3_ANOMALY", score=Decimal(str(score)), confidence=Decimal(str(confidence)),
        reason=reason, evidence=evidence, model_version="task3-r1", rule_version="task3-r1",
    )
    task, _ = _open_review_task(
        tenant=tenant, program=program, task_type=ReviewTask.TaskType.RISK_REVIEW,
        entity_type=entity_type, entity_id=entity_id, priority=ReviewTask.Priority.HIGH,
        resolution=f"Review anomaly signal {signal.id} before the operation is submitted.",
    )
    return signal, task


def validate_task3_cash_amount(*, program, beneficiary, proposed_amount, entity_type="PAYMENT_INSTRUCTION", entity_id=""):
    """Validate CASH-R1 formula and create an anomaly review for out-of-formula amounts."""
    if not is_task3_cash_program(program):
        return {"valid": True, "applies": False, "expected_amount": None}
    try:
        expected = calculate_cash_transfer_amount(beneficiary.household.household_size)
    except ValueError as exc:
        reason = f"Invalid household size: {exc}"
        signal, task = create_anomaly_review(
            tenant=program.tenant, program=program, entity_type=entity_type, entity_id=entity_id or beneficiary.id,
            reason=reason, evidence={"household_id": str(beneficiary.household_id), "household_size": beneficiary.household.household_size},
        )
        return {"valid": False, "applies": True, "expected_amount": None, "reason": reason, "signal_id": str(signal.id), "review_task_id": str(task.id)}

    amount = _safe_decimal(proposed_amount)
    if amount != expected:
        reason = f"Proposed CASH-R1 amount {amount:.2f} is outside the household-size formula; expected {expected:.2f}."
        signal, task = create_anomaly_review(
            tenant=program.tenant, program=program, entity_type=entity_type, entity_id=entity_id or beneficiary.id,
            reason=reason, evidence={
                "household_id": str(beneficiary.household_id),
                "household_size": beneficiary.household.household_size,
                "proposed_amount": str(amount),
                "expected_amount": str(expected),
                "cap": str(TASK3_CASH_MAX),
            },
        )
        return {"valid": False, "applies": True, "expected_amount": expected, "reason": reason, "signal_id": str(signal.id), "review_task_id": str(task.id)}
    return {"valid": True, "applies": True, "expected_amount": expected}


@transaction.atomic
def record_offline_event(*, tenant, operation_id, operation_type, payload, client_generated_id="", actor=None):
    """Idempotent receipt for an offline event. Same payload is acknowledged; changed payload is rejected."""
    import json
    canonical = json.dumps(payload or {}, sort_keys=True, default=str, separators=(",", ":"))
    request_hash = sha256(canonical.encode()).hexdigest()
    existing = SyncOperation.objects.select_for_update().filter(tenant=tenant, operation_id=operation_id).first()
    if existing:
        if existing.request_hash != request_hash:
            existing.status = SyncOperation.Status.REJECTED
            existing.error_message = "Same operation_id was replayed with a different payload."
            existing.processed_at = timezone.now()
            existing.save(update_fields=["status", "error_message", "processed_at"])
            if actor:
                AuditEvent.objects.create(tenant=tenant, actor=actor, action="OFFLINE_REPLAY_REJECTED", entity_type="SYNC_OPERATION", entity_id=str(existing.id), after={"operation_id": operation_id, "reason": "payload_mismatch"})
            return {"status": "conflict", "applied": False, "replay": False, "operation_id": operation_id}
        if actor:
            AuditEvent.objects.create(tenant=tenant, actor=actor, action="OFFLINE_REPLAY_ACKNOWLEDGED", entity_type="SYNC_OPERATION", entity_id=str(existing.id), after={"operation_id": operation_id, "status": existing.status})
        return {"status": "duplicate", "applied": False, "replay": True, "operation_id": operation_id}

    sync = SyncOperation.objects.create(
        tenant=tenant, operation_id=operation_id, operation_type=operation_type,
        client_generated_id=client_generated_id, request_hash=request_hash,
        status=SyncOperation.Status.APPLIED, processed_at=timezone.now(),
        validation_results={"offline_replay_safe": True},
    )
    if actor:
        AuditEvent.objects.create(tenant=tenant, actor=actor, action="OFFLINE_EVENT_APPLIED", entity_type="SYNC_OPERATION", entity_id=str(sync.id), after={"operation_id": operation_id, "operation_type": operation_type})
    return {"status": "applied", "applied": True, "replay": False, "operation_id": operation_id}


def task3_cash_expected_summary(request_rows):
    """Pure, reproducible CASH-R1 arithmetic for acceptance evidence; does not alter source decisions."""
    eligible = []
    holds = []
    for row in request_rows:
        size = row.get("size")
        site = str(row.get("site", "")).upper()
        consent = str(row.get("consent", "")).upper()
        status = str(row.get("status", "")).upper()
        household_id = str(row.get("household_id", "")).strip()
        if not household_id or not isinstance(size, (int, float)) or size <= 0:
            holds.append({"row_id": row.get("row_id"), "reason": "CORRECTION_HOLD"}); continue
        if consent != "YES":
            holds.append({"row_id": row.get("row_id"), "reason": "CONSENT_HOLD"}); continue
        if site != "A":
            holds.append({"row_id": row.get("row_id"), "reason": "SITE_HOLD"}); continue
        if status != "APPROVED":
            holds.append({"row_id": row.get("row_id"), "reason": "APPROVAL_HOLD"}); continue
        eligible.append(row)

    seen = set()
    unique = []
    for row in eligible:
        hid = str(row["household_id"])
        if hid in seen:
            holds.append({"row_id": row.get("row_id"), "reason": "DUPLICATE_REVIEW", "household_id": hid}); continue
        seen.add(hid); unique.append(row)
    principal = sum((calculate_cash_transfer_amount(row["size"]) for row in unique), Decimal("0"))
    fees = Decimal(len(unique)) * TASK3_CASH_FEE
    return {"eligible_count": len(unique), "principal": str(principal), "fees": str(fees), "debit": str(principal + fees), "holds": holds}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _safe_decimal(value, default="0"):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _open_review_task(*, tenant, program, task_type, entity_type, entity_id, priority="MEDIUM", resolution=""):
    """Idempotently create a human-review task for an AI/automation exception."""
    task, created = ReviewTask.objects.get_or_create(
        tenant=tenant,
        program=program,
        task_type=task_type,
        entity_type=entity_type,
        entity_id=str(entity_id),
        status=ReviewTask.Status.OPEN,
        defaults={
            "priority": priority,
            "resolution": resolution,
        },
    )
    return task, created


def _open_signal(*, tenant, program, entity_type, entity_id, signal_type, score, confidence, reason, evidence=None, model_version="", rule_version=""):
    """Create one open signal per entity/type/version; never mutate source data."""
    signal = AISignal.objects.filter(
        tenant=tenant,
        entity_type=entity_type,
        entity_id=str(entity_id),
        signal_type=signal_type,
        model_version=model_version,
        status=AISignal.Status.OPEN,
    ).first()
    if signal:
        return signal, False

    signal = AISignal.objects.create(
        tenant=tenant,
        program=program,
        entity_type=entity_type,
        entity_id=str(entity_id),
        signal_type=signal_type,
        score=_safe_decimal(score),
        confidence=_safe_decimal(confidence),
        reason=reason,
        evidence=evidence or {},
        model_version=model_version,
        rule_version=rule_version,
        status=AISignal.Status.OPEN,
    )
    return signal, True


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def run_deduplication_check(beneficiary):
    """Compatibility wrapper used by imports and the API."""
    from .ai.duplicate_detector import DuplicateDetector

    results = DuplicateDetector.detect_for(beneficiary.id)
    return bool(results)


# ---------------------------------------------------------------------------
# Data quality + post-sync validation
# ---------------------------------------------------------------------------

def validate_beneficiary_data(beneficiary):
    """Explainable, non-destructive data-quality checks for a beneficiary."""
    issues = []

    if not beneficiary.full_name or len(beneficiary.full_name.strip()) < 2:
        issues.append({"code": "MISSING_NAME", "severity": "HIGH", "message": "Beneficiary full name is missing or too short."})
    if beneficiary.phone_last4 and (len(beneficiary.phone_last4) != 4 or not beneficiary.phone_last4.isdigit()):
        issues.append({"code": "INVALID_PHONE_LAST4", "severity": "MEDIUM", "message": "Phone last four digits must contain exactly four digits."})
    if not beneficiary.consent_given:
        issues.append({"code": "MISSING_CONSENT", "severity": "HIGH", "message": "Recorded consent is missing."})
    if not beneficiary.household_id:
        issues.append({"code": "MISSING_HOUSEHOLD", "severity": "CRITICAL", "message": "Beneficiary is not linked to a household."})
    elif beneficiary.household.program.tenant_id != beneficiary.household.tenant_id:
        issues.append({"code": "TENANT_PROGRAM_MISMATCH", "severity": "CRITICAL", "message": "Household and program do not belong to the same tenant."})

    return issues


def run_data_quality_check(beneficiary, create_tasks=True):
    issues = validate_beneficiary_data(beneficiary)
    if not issues:
        return {"valid": True, "issues": [], "signals_created": 0, "tasks_created": 0}

    tenant = beneficiary.household.tenant
    program = beneficiary.household.program
    highest = "CRITICAL" if any(i["severity"] == "CRITICAL" for i in issues) else "HIGH" if any(i["severity"] == "HIGH" for i in issues) else "MEDIUM"
    reason = "Data-quality validation found {} issue(s).".format(len(issues))
    signal, signal_created = _open_signal(
        tenant=tenant,
        program=program,
        entity_type="BENEFICIARY",
        entity_id=beneficiary.id,
        signal_type="DATA_QUALITY",
        score=Decimal("0.80") if highest in {"HIGH", "CRITICAL"} else Decimal("0.60"),
        confidence=Decimal("0.95"),
        reason=reason,
        evidence={"issues": issues},
        model_version="data-quality-v1",
        rule_version="data-quality-rules-v1",
    )
    task_created = False
    if create_tasks:
        _, task_created = _open_review_task(
            tenant=tenant,
            program=program,
            task_type=ReviewTask.TaskType.DATA_QUALITY,
            entity_type="BENEFICIARY",
            entity_id=beneficiary.id,
            priority=ReviewTask.Priority.CRITICAL if highest == "CRITICAL" else ReviewTask.Priority.HIGH,
            resolution=reason,
        )
    return {"valid": False, "issues": issues, "signals_created": int(signal_created), "tasks_created": int(task_created), "signal_id": str(signal.id)}


@transaction.atomic
def validate_synced_registration(sync_operation, household, beneficiaries):
    """Validate data after an offline registration is applied to the server."""
    results = []
    for beneficiary in beneficiaries:
        result = run_data_quality_check(beneficiary, create_tasks=True)
        duplicate_results = run_deduplication_check(beneficiary)
        result["duplicate_flagged"] = duplicate_results
        results.append({"beneficiary_id": str(beneficiary.id), **result})

    sync_operation.validation_results = {
        "beneficiary_count": len(beneficiaries),
        "results": results,
    }
    has_errors = any(not item["valid"] or item["duplicate_flagged"] for item in results)
    sync_operation.status = SyncOperation.Status.CONFLICT if has_errors else SyncOperation.Status.APPLIED
    sync_operation.processed_at = timezone.now()
    sync_operation.save(update_fields=["validation_results", "status", "processed_at"])
    return results


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

@transaction.atomic
def run_automated_reconciliation(batch_id, provider_report_data):
    """Reconcile a payment batch without overwriting provider evidence."""
    batch = PaymentBatch.objects.select_related("tenant", "program").filter(pk=batch_id).first()
    if not batch:
        raise ValueError("Payment batch not found")
    if not isinstance(provider_report_data, dict):
        raise ValueError("provider_report_data must be an object keyed by provider reference")

    results = {"matched": 0, "mismatched": 0, "unresolved": 0, "items_created": 0}
    instructions = batch.instructions.select_related("beneficiary").all()

    for instruction in instructions:
        reference = instruction.provider_reference or ""
        provider_record = provider_report_data.get(reference) if reference else None

        if not provider_record:
            issue_type = ReconciliationItem.IssueType.MISSING_REFERENCE
            item, created = ReconciliationItem.objects.get_or_create(
                tenant=batch.tenant,
                program=batch.program,
                instruction=instruction,
                status=ReconciliationItem.Status.OPEN,
                defaults={
                    "issue_type": issue_type,
                    "expected_amount": instruction.amount,
                    "expected_status": instruction.status,
                    "provider_reference": reference,
                },
            )
            results["unresolved"] += 1
            results["items_created"] += int(created)
            _open_review_task(
                tenant=batch.tenant,
                program=batch.program,
                task_type=ReviewTask.TaskType.RECONCILIATION,
                entity_type="PAYMENT_INSTRUCTION",
                entity_id=instruction.id,
                priority=ReviewTask.Priority.HIGH,
                resolution="Payment instruction was not found in the provider report.",
            )
            continue

        provider_amount = _safe_decimal(provider_record.get("amount"))
        provider_status = str(provider_record.get("status", "UNKNOWN")).upper()
        expected_amount = instruction.amount
        expected_status = instruction.status

        if provider_status == "SUCCESS" and provider_amount == expected_amount:
            results["matched"] += 1
            ReconciliationItem.objects.filter(instruction=instruction, status=ReconciliationItem.Status.OPEN).update(
                status=ReconciliationItem.Status.RESOLVED,
                actual_amount=provider_amount,
                actual_status=provider_status,
                resolution_note="Automatically matched against provider report.",
                resolved_at=timezone.now(),
            )
        else:
            results["mismatched"] += 1
            if provider_status == "FAILED":
                issue_type = ReconciliationItem.IssueType.FAILED
            elif provider_status in {"REVERSED", "REFUNDED"}:
                issue_type = ReconciliationItem.IssueType.REVERSED
            elif provider_amount != expected_amount:
                issue_type = ReconciliationItem.IssueType.AMOUNT_MISMATCH
            else:
                issue_type = ReconciliationItem.IssueType.STATUS_MISMATCH

            _, created = ReconciliationItem.objects.get_or_create(
                tenant=batch.tenant,
                program=batch.program,
                instruction=instruction,
                status=ReconciliationItem.Status.OPEN,
                defaults={
                    "issue_type": issue_type,
                    "expected_amount": expected_amount,
                    "actual_amount": provider_amount,
                    "expected_status": expected_status,
                    "actual_status": provider_status,
                    "provider_reference": reference,
                },
            )
            results["items_created"] += int(created)
            _open_review_task(
                tenant=batch.tenant,
                program=batch.program,
                task_type=ReviewTask.TaskType.RECONCILIATION,
                entity_type="PAYMENT_INSTRUCTION",
                entity_id=instruction.id,
                priority=ReviewTask.Priority.HIGH,
                resolution=f"Provider reconciliation exception: {issue_type}.",
            )

    if results["matched"] and results["mismatched"]:
        batch.status = PaymentBatch.Status.PARTIAL_FAILURE
    elif results["mismatched"] or results["unresolved"]:
        batch.status = PaymentBatch.Status.FAILED
    else:
        batch.status = PaymentBatch.Status.RECONCILED
    batch.save(update_fields=["status"])
    return results


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------

@transaction.atomic
def run_anomaly_detection(payment_batch):
    """Detect explainable payment anomalies and create human-review tasks."""
    batch = payment_batch if isinstance(payment_batch, PaymentBatch) else PaymentBatch.objects.filter(pk=payment_batch).first()
    if not batch:
        raise ValueError("Payment batch not found")

    anomalies = 0
    for instruction in batch.instructions.select_related("beneficiary").all():
        anomaly_reasons = []
        amount = instruction.amount
        if amount > Decimal("500.00"):
            anomaly_reasons.append({"code": "HIGH_AMOUNT", "message": f"Amount {amount} exceeds configured threshold 500.00."})

        recent_same_beneficiary = PaymentInstruction.objects.filter(
            batch=batch,
            beneficiary=instruction.beneficiary,
        ).exclude(pk=instruction.pk).count()
        if recent_same_beneficiary >= 2:
            anomaly_reasons.append({"code": "REPEATED_PAYMENT_PATTERN", "message": "Multiple payment instructions exist for the same beneficiary."})

        if not anomaly_reasons:
            continue

        anomalies += 1
        signal, _ = _open_signal(
            tenant=batch.tenant,
            program=batch.program,
            entity_type="PAYMENT_INSTRUCTION",
            entity_id=instruction.id,
            signal_type="PAYMENT_ANOMALY",
            score=Decimal("0.85"),
            confidence=Decimal("0.80"),
            reason="; ".join(item["message"] for item in anomaly_reasons),
            evidence={"rules": anomaly_reasons, "amount": str(amount), "beneficiary_id": str(instruction.beneficiary_id)},
            model_version="anomaly-v2",
            rule_version="anomaly-rules-v2",
        )
        _open_review_task(
            tenant=batch.tenant,
            program=batch.program,
            task_type=ReviewTask.TaskType.RISK_REVIEW,
            entity_type="PAYMENT_INSTRUCTION",
            entity_id=instruction.id,
            priority=ReviewTask.Priority.HIGH,
            resolution=f"Review anomaly signal {signal.id} before payment is treated as cleared.",
        )
    return anomalies


# ---------------------------------------------------------------------------
# Configurable exception automation
# ---------------------------------------------------------------------------

def _conditions_match(conditions, payload):
    for key, expected in (conditions or {}).items():
        actual = payload.get(key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


@transaction.atomic
def process_automation_event(*, tenant, event_name, payload, program=None, dry_run=False):
    """Execute safe, idempotent exception automation. Financial actions are blocked by design."""
    rules = AutomationRule.objects.filter(tenant=tenant, event_name=event_name, is_active=True).order_by("priority", "created_at")
    results = []
    for rule in rules:
        if rule.program_id and (not program or rule.program_id != program.id):
            continue
        if not _conditions_match(rule.conditions, payload):
            continue

        operation_id = payload.get("operation_id") or payload.get("idempotency_key") or str(uuid4())
        key = sha256(f"{tenant.id}:{rule.id}:{operation_id}".encode()).hexdigest()
        existing = AutomationExecution.objects.filter(idempotency_key=key).first()
        if existing:
            results.append({"rule_id": str(rule.id), "execution_id": str(existing.id), "status": "DUPLICATE"})
            continue

        action = rule.action_name.upper()
        planned = {"action": action, "payload": payload}
        execution = AutomationExecution.objects.create(
            tenant=tenant,
            rule=rule,
            idempotency_key=key,
            event_payload=payload,
            planned_action=planned,
            status=AutomationExecution.Status.DRY_RUN if dry_run else AutomationExecution.Status.BLOCKED,
        )

        if dry_run:
            results.append({"rule_id": str(rule.id), "execution_id": str(execution.id), "status": execution.status})
            continue

        # Safe actions only: route/flag/review. No automatic payment movement or eligibility decisions.
        if action == "CREATE_REVIEW_TASK":
            task_type = rule.action_config.get("task_type", ReviewTask.TaskType.DATA_QUALITY)
            if task_type not in {choice[0] for choice in ReviewTask.TaskType.choices}:
                execution.status = AutomationExecution.Status.FAILED
                execution.save(update_fields=["status"])
                results.append({"rule_id": str(rule.id), "execution_id": str(execution.id), "status": execution.status})
                continue
            _open_review_task(
                tenant=tenant,
                program=program,
                task_type=task_type,
                entity_type=payload.get("entity_type", "AUTOMATION_EVENT"),
                entity_id=str(payload.get("entity_id", operation_id)),
                priority=rule.action_config.get("priority", ReviewTask.Priority.MEDIUM),
                resolution=rule.action_config.get("resolution", f"Automation rule {rule.id} routed an exception for human review."),
            )
            execution.status = AutomationExecution.Status.COMPLETED
        elif action == "CREATE_AI_SIGNAL":
            _open_signal(
                tenant=tenant,
                program=program,
                entity_type=payload.get("entity_type", "AUTOMATION_EVENT"),
                entity_id=str(payload.get("entity_id", operation_id)),
                signal_type=rule.action_config.get("signal_type", "AUTOMATION_EXCEPTION"),
                score=rule.action_config.get("score", "0.70"),
                confidence=rule.action_config.get("confidence", "0.80"),
                reason=rule.action_config.get("reason", "Automation rule created an explainable signal."),
                evidence=payload,
                model_version="automation-v1",
                rule_version=str(rule.id),
            )
            execution.status = AutomationExecution.Status.COMPLETED
        elif action == "FLAG_DATA_QUALITY":
            _open_review_task(
                tenant=tenant,
                program=program,
                task_type=ReviewTask.TaskType.DATA_QUALITY,
                entity_type=payload.get("entity_type", "AUTOMATION_EVENT"),
                entity_id=str(payload.get("entity_id", operation_id)),
                priority=ReviewTask.Priority.HIGH,
                resolution=rule.action_config.get("resolution", "Data-quality exception requires human review."),
            )
            execution.status = AutomationExecution.Status.COMPLETED
        else:
            execution.status = AutomationExecution.Status.BLOCKED

        execution.save(update_fields=["status"])
        results.append({"rule_id": str(rule.id), "execution_id": str(execution.id), "status": execution.status})
    return results


# ---------------------------------------------------------------------------
# Executive AI summary
# ---------------------------------------------------------------------------

class AICopilotService:
    @staticmethod
    def generate_system_summary_report(tenant=None):
        beneficiary_qs = Beneficiary.objects.all()
        signal_qs = AISignal.objects.all()
        task_qs = ReviewTask.objects.all()
        if tenant:
            beneficiary_qs = beneficiary_qs.filter(household__tenant=tenant)
            signal_qs = signal_qs.filter(tenant=tenant)
            task_qs = task_qs.filter(tenant=tenant)

        total_beneficiaries = beneficiary_qs.count()
        total_signals = signal_qs.count()
        open_tasks = task_qs.filter(status=ReviewTask.Status.OPEN).count()
        high_priority_tasks = task_qs.filter(status=ReviewTask.Status.OPEN, priority__in=[ReviewTask.Priority.HIGH, ReviewTask.Priority.CRITICAL]).count()
        duplicates_count = signal_qs.filter(signal_type="POSSIBLE_DUPLICATE", status=AISignal.Status.OPEN).count()
        anomalies_count = signal_qs.filter(signal_type__in=["PAYMENT_ANOMALY", "HIGH_AMOUNT_ANOMALY"], status=AISignal.Status.OPEN).count()
        data_quality_count = signal_qs.filter(signal_type="DATA_QUALITY", status=AISignal.Status.OPEN).count()

        return {
            "summary": "Operational AI summary generated from verified platform records.",
            "beneficiaries": total_beneficiaries,
            "open_ai_signals": total_signals,
            "open_review_tasks": open_tasks,
            "high_or_critical_tasks": high_priority_tasks,
            "open_duplicate_signals": duplicates_count,
            "open_anomaly_signals": anomalies_count,
            "open_data_quality_signals": data_quality_count,
            "human_review_required": high_priority_tasks > 0,
            "generated_at": timezone.now().isoformat(),
        }
