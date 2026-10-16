# MannaBooks API

FastAPI backend for MannaBooks: multi-tenant accounting/ERP for Kenyan businesses (documents, payments,
inventory, payroll, CRM, KRA eTIMS). The Next.js app (`mannabooks`) is the only client; browsers reach this
API through the Next.js `/api/v1` rewrite.

## Stack
Python 3.13 · FastAPI · SQLAlchemy 2 (async) + asyncpg · PostgreSQL · Alembic · Redis (rate limits) · PyJWT

## Local development

```bash
docker compose up -d
```
Starts Postgres on 5432, a throwaway test Postgres on 5433, and Redis on 6379.

```bash
python -m venv .venv
```
Then activate it (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate` elsewhere) and install:

```bash
pip install -r requirements-dev.txt
```

Copy `.env.example` to `.env` and fill it in (generate secrets as described in the file), then create the schema:

```bash
python -m alembic upgrade head
```

```bash
uvicorn app.main:app --reload --port 8000
```

`ENVIRONMENT=development` refuses to connect to a non-local database unless
`ALLOW_REMOTE_DB_IN_DEVELOPMENT=true` is set on purpose.

## Tests

```bash
python -m pytest -q
```
`tests/conftest.py` points the app at `TEST_DATABASE_URL` (default: the compose `test-db` on port 5433) and
refuses to run against a non-local database. The auth, rate-limit, refresh-token and payroll tests need no database.

## Database migrations
Schema changes go **only** through Alembic revisions in `alembic/versions/` (never `scripts/init_db.py`, which
is kept for throwaway local databases via `AUTO_INIT_DB=true`).

```bash
python -m alembic revision -m "describe the change"
```
Write idempotent SQL where possible, then `python -m alembic upgrade head`. Railway runs
`python -m alembic upgrade head` as a pre-deploy step (`railway.json`).

## How requests are authorised
- `app/api/deps.py`: identifies the caller. A user comes from a JWT (`Authorization: Bearer` from the Next.js
  server, or the HttpOnly `access_token` cookie through the rewrite). The Next.js server itself is identified
  by `X-Internal-Key`. An invalid token is always rejected. Cookie-authenticated writes need `X-Requested-With`.
- `app/api/tenancy.py`: `workspace_guard` is mounted on every workspace router in `app/main.py`. It requires a
  signed-in active member of every workspace the request references (`shop_id`/slug, or resource ids such as
  `document_id`) and applies role permissions. The internal key alone never unlocks tenant data.
- Sessions (`app/modules/auth/tokens.py`): 15-minute access tokens plus 30-day rotating refresh tokens with
  reuse detection and a 12-hour idle timeout. `/v1/auth/refresh` is internal-only (called by the Next.js proxy).
- Money: always `Decimal` (`app/core/money.py`). Timestamps are naive UTC (`app/core/datetime_utils.utcnow`).

## Layout
```
app/
  api/            auth dependencies and the tenant guard
  core/           config, security, logging, rate limiting, money/time helpers
  db/             engine/session, Base, all_models (model registry for Alembic)
  models/         SQLAlchemy models
  modules/<area>/ router (HTTP) + service (logic) + schemas per business area
  workers/        background workers (eTIMS queue)
alembic/          migrations
scripts/          one-off admin scripts (make_admin.py, init_db.py)
tests/
```

## Deployment (Railway)
`railway.json`: Nixpacks build (Python version from `.python-version`), pre-deploy migrations, uvicorn with
`--proxy-headers` and `WEB_CONCURRENCY` workers (default 2). Liveness `/health`, readiness `/health/ready` (DB check).
A `Dockerfile` is included if you prefer image builds. Required environment variables are in `.env.example` and
`../ENVIRONMENT_VARIABLES.md`.

## Admin access
Super admins are created from a shell only:

```bash
python scripts/make_admin.py user@example.com
```
