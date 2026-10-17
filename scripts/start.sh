#!/bin/sh
# Container entrypoint: apply database migrations, then start the API.
# - Migrations hold a Postgres advisory lock (alembic/env.py), so concurrent containers are safe.
# - If a migration fails, the script exits non-zero and the API never starts; the platform keeps the
#   previous deployment running instead of serving code against an outdated schema.
set -e

echo "[start] Applying database migrations..."
python -m alembic upgrade head
echo "[start] Migrations complete. Starting API."

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips='*' \
  --workers "${WEB_CONCURRENCY:-2}"
