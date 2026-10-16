from functools import lru_cache
from typing import Literal
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["text", "json"] = "text"

    # PostgreSQL Connection (required)
    DATABASE_URL: str

    # Internal API Secret between Next.js BFF and FastAPI (required)
    INTERNAL_API_KEY: str

    # Cron secret (required)
    CRON_SECRET: str

    # Session & Cookie
    COOKIE_NAME: str = "access_token"
    COOKIE_DURATION_DAYS: int = 30

    # JWT Settings (required)
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 15  # short-lived access token; renewed silently with the refresh token

    # Rotating refresh tokens
    REFRESH_TOKEN_DAYS: int = 30  # absolute session lifetime from sign-in
    SESSION_IDLE_HOURS: int = 12  # sign out after this long without any activity
    REFRESH_REUSE_GRACE_SECONDS: int = 60  # tolerate parallel refreshes from the same browser

    # Business Timezone
    TZ: str = "Africa/Nairobi"

    # KRA eTIMS
    ENABLE_KRA_ETIMS_GLOBAL: bool = False
    KRA_ETIMS_MOCK_MODE: bool = False
    KRA_ETIMS_ENV: Literal["SANDBOX", "PRODUCTION"] = "SANDBOX"
    KRA_ETIMS_BASE_URL: str = "https://timsvscu.kra.go.ke/etims-api"
    KRA_ETIMS_AES_ENCRYPTION_KEY: str  # required

    # API Docs Basic Auth credentials ("user:pass,user2:pass2"). Empty = docs disabled.
    DOCS_USERS: str = ""
    # Swagger/OpenAPI is never served in production unless explicitly enabled.
    ENABLE_API_DOCS: bool = False

    # Comma-separated list of browser origins allowed by CORS.
    CORS_ORIGINS: str = "http://localhost:3000"

    # Shared store for rate limits (Railway Redis plugin exposes REDIS_URL). Empty = in-memory fallback.
    REDIS_URL: str = ""

    # Legacy behaviour: run scripts/init_db.py (create_all + ALTERs) on startup.
    # Production/staging use `alembic upgrade head` instead.
    AUTO_INIT_DB: bool = False

    # Safety net: refuse to start against a non-local database unless explicitly allowed.
    ALLOW_REMOTE_DB_IN_DEVELOPMENT: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def _validate_secrets(self):
        weak = []
        if len(self.JWT_SECRET) < 32:
            weak.append("JWT_SECRET (min 32 chars)")
        if len(self.INTERNAL_API_KEY) < 32:
            weak.append("INTERNAL_API_KEY (min 32 chars)")
        if len(self.CRON_SECRET) < 24:
            weak.append("CRON_SECRET (min 24 chars)")
        if weak and self.ENVIRONMENT != "development":
            raise ValueError("Insecure configuration: " + ", ".join(weak))
        if self.ENVIRONMENT == "development" and not self.ALLOW_REMOTE_DB_IN_DEVELOPMENT:
            from urllib.parse import urlparse

            host = (urlparse(self.async_database_url).hostname or "").lower()
            if host not in {"localhost", "127.0.0.1", "::1", "db", "postgres"}:
                raise ValueError(
                    f"Development is pointed at a remote database ({host}). Use a local Postgres "
                    "(see docker-compose.yml) or set ALLOW_REMOTE_DB_IN_DEVELOPMENT=true deliberately."
                )
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def docs_enabled(self) -> bool:
        if not self.docs_credentials_dict:
            return False
        return self.ENABLE_API_DOCS or not self.is_production

    @property
    def docs_credentials_dict(self) -> dict[str, str]:
        """Parses comma-separated 'username:password,user2:pass2' into a dictionary."""
        creds = {}
        if not self.DOCS_USERS:
            return creds
        for pair in self.DOCS_USERS.split(","):
            pair = pair.strip()
            if ":" in pair:
                user, pwd = pair.split(":", 1)
                creds[user.strip()] = pwd.strip()
        return creds

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"

    @property
    def is_staging(self) -> bool:
        return self.ENVIRONMENT == "staging"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def async_database_url(self) -> str:
        url = self.DATABASE_URL
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
