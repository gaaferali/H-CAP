# H-CAP  MVP

Scalable humanitarian cash assistance platform.

## Stack

- Backend: Django REST Framework with PostgreSQL
- Frontend: Vite `8.3.0`, React `19.3.0`, TypeScript `7.0.2`, Bootstrap `5.3.8`
- Payments: fake simulated adapter only
- AI/automation: advisory signals and controlled human-review workflows only; no automated eligibility, fraud, or payment decision

## Run platform 

Download docker
make sure that docker is open and running 
than in vscode terminal just run: 
docker compose up --build
open locallhost

email gaafer@gmail.com
password gaafer

For local backend development, start the database first with `docker compose up -d db`, then run `backend\.venv\Scripts\python.exe backend\manage.py runserver`. for testing `backend python manage.py test` or `.\.venv\Scripts\python.exe manage.py test core  ` The default local database port is `5433`, matching `docker-compose.yml`.

## Implemented Scope


- program KPIs.
- Mand fake payment simulation foundations.

### Household-centered operations

Eligibility, enrollment, cash/NFI entitlements, distribution allocations, and delivery review are household-driven. Beneficiaries remain household members and are retained as secondary identity context. Program selectors must be chosen before dependent household/member selectors; the API validates tenant and program relationships independently of the UI.

Cash entitlements are checked transactionally against the selected program budget. NFI entitlements lock the catalogue item while checking available stock. Cancellation, edits, allocation, and delivery update the corresponding capacity and audit records. Existing beneficiary-level records are preserved through nullable compatibility links and backfilled household links by migration `0015_household_centered_workflow`.

The household registration reference replaces the redundant client-generated model field, and phone masking is derived from the authoritative full phone number. National IDs remain hashed and are never returned raw. Payment integrations remain simulated; payment events are append-only/corrective and human review remains advisory.

Run migrations with `python manage.py migrate`, backend tests with `python manage.py test`, and the frontend production check with `npm.cmd run build` from `frontend`.
