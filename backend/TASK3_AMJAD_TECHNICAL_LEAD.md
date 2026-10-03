# Task 3 — Amjad Nazar Technical Lead Controls

This backend update implements the controls assigned to Amjad in the ATC Academy Task 3 acceptance packs.

## Implemented controls

- **CASH-R1 formula guard:** 100 for household size 1–4, +20 for each member above four, capped at 160.
- **Out-of-formula anomaly blocking:** a proposed amount such as TEST-USD 5,000 is rejected before provider submission and creates an `AISignal` plus `ReviewTask`.
- **Duplicate provider callback protection:** a repeated settled callback with the same provider reference is acknowledged without a second event/posting.
- **Timeout/UNKNOWN protection:** a provider timeout is recorded as `UNKNOWN` while the instruction remains pending/submitted; it is not treated as permission to pay again.
- **Failed-payment retry:** retry creates a new linked payment attempt while preserving the original failed instruction/event history.
- **Offline replay idempotency:** `/api/sync/offline-event/` applies an operation once, acknowledges an identical replay, and rejects a payload mismatch under the same operation key.
- **AI signal approval permissions:** only ADMIN/REVIEWER/MANAGER can review an AI signal; denied attempts are audited.
- **Existing controls retained:** duplicate detection, automation execution idempotency, anomaly detection, audit events, tenant isolation, registration sync idempotency and complaint AI.

## New endpoint

`POST /api/sync/offline-event/`

Example payload:

```json
{
  "operation_id": "OFFLINE-001",
  "operation_type": "CASH_REQUEST",
  "payload": {"request_id": "C01", "amount": "100.00"}
}
```

First upload is `applied`; an identical replay is `duplicate`; the same operation ID with a different payload is `conflict`.

## Evidence tests

Run:

```bash
python manage.py test core.tests.test_task3_controls
```

The test suite covers the CASH-R1 formula/cap, CAS06-style anomaly blocking, X02 offline replay idempotency, and the CASH baseline arithmetic oracle.

## Important scope

These changes cover the technical-control responsibilities assigned to Amjad. They do **not** claim that the full NFI warehouse workflow or the full MEDICAL FEFO/referral workflow has been implemented by this file alone; those remain module/backend work for the assigned owners.
