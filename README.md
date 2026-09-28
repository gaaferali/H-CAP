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

For local backend development, start the database first with `docker compose up -d db`, then run `backend\.venv\Scripts\python.exe backend\manage.py runserver`. for testing `backend python manage.py test` or `.\.venv\Scripts\python.exe manage.py test core  ` The default local database port is `5433`, matching `docker-compose.yml`.

## Implemented Scope


- program KPIs.
- Mand fake payment simulation foundations.
