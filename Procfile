release: python -m alembic upgrade head
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips=* --workers ${WEB_CONCURRENCY:-2}
