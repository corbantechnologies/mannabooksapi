"""
Test bootstrap. Tests write data, so they must NEVER run against a shared/remote database.

Set TEST_DATABASE_URL (default: the docker-compose `test-db` on localhost:5433).
"""
import os
from urllib.parse import urlparse

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5433/mannabooks_test"
)
_host = (urlparse(TEST_DB).hostname or "").lower()
if _host not in {"localhost", "127.0.0.1", "::1", "test-db", "postgres"}:
    raise RuntimeError(f"Refusing to run tests against non-local database host '{_host}'.")

# Environment variables take precedence over .env in pydantic-settings.
os.environ["DATABASE_URL"] = TEST_DB
os.environ["ENVIRONMENT"] = "development"
os.environ.setdefault("AUTO_INIT_DB", "true")
os.environ.setdefault("DOCS_USERS", "admin:test-docs-password")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
