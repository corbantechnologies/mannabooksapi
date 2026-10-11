from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

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
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Business Timezone
    TZ: str = "Africa/Nairobi"

    # KRA eTIMS
    ENABLE_KRA_ETIMS_GLOBAL: bool = False
    KRA_ETIMS_MOCK_MODE: bool = False
    KRA_ETIMS_ENV: Literal["SANDBOX", "PRODUCTION"] = "SANDBOX"
    KRA_ETIMS_BASE_URL: str = "https://timsvscu.kra.go.ke/etims-api"
    KRA_ETIMS_AES_ENCRYPTION_KEY: str  # required

    # API Docs Basic Auth credentials
    DOCS_USERS: str = "admin:mannabooks2026,team:stagingpass2026"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
