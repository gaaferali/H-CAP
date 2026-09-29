# GHCAP — Amjad AI, Automation & Data Engineering Hardening

## Responsibilities covered

- Duplicate signals across modalities: improved name/phone/national-ID/DOB/gender/location matching, explainable evidence, confidence, idempotent signal/task creation.
- Anomaly signals: high-amount and repeated-payment-pattern rules, explainable evidence, human review tasks.
- Explainability: AISignal reason/evidence/confidence/model_version/rule_version retained.
- Human-review tasks: duplicate, risk, data-quality, reconciliation and complaint escalation tasks; repeated runs do not create task storms.
- Exception automation: configurable AutomationRule execution with idempotency and safe actions only (review task, AI signal, data-quality flag). No automatic financial movement or eligibility decisions.
- Idempotency: offline registration sync now requires operation_id/idempotency_key and hashes the payload; same operation can be retried safely; changed payload is rejected.
- Data quality: non-destructive beneficiary validation with DATA_QUALITY AISignal + ReviewTask.
- Post-sync validation: offline registrations are validated after server application; duplicate and data-quality results are returned and persisted.
- Complaint AI: human review endpoint now records ACCEPT/MODIFY/DISMISS, updates the AI signal and closes the related review task without resolving the underlying complaint automatically.
- AI reporting: system report now includes tenant-scoped AI signal/task/data-quality/anomaly/duplicate metrics.

## New API endpoints

- `POST /api/ai/data-quality/<beneficiary_id>/`
- `POST /api/ai/automation/events/`
- `POST /api/ai/complaints/<complaint_id>/review/`
- `GET /api/sync-operations/`
- `GET /api/ai-signals/`
- `GET /api/automation-rules/`
- `GET /api/automation-executions/`

Existing complaint AI, deduplication, anomaly and reconciliation endpoints remain available.

## Offline sync contract

The client should send `operation_id` (or `idempotency_key`) on every offline registration sync. The server responds with:

- `APPLIED` — sync applied and post-sync validation completed without flags.
- `CONFLICT` — records were applied but data-quality/duplicate review is required.
- `REJECTED` — idempotency conflict or invalid operation.
- `duplicate` response — safe retry of the exact same operation.

## Safety boundary

AI/automation is advisory and exception-routing only. It does not silently change source beneficiary data, approve eligibility, release money, or resolve a beneficiary complaint automatically.
