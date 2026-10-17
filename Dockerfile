# Production image for the MannaBooks API (alternative to Railway's Nixpacks builder).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
# Applies migrations (under an advisory lock) and then starts uvicorn.
CMD ["sh", "scripts/start.sh"]
