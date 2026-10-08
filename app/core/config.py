from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # PostgreSQL Connection (Strictly from environment)
    DATABASE_URL: str

    # Internal API Secret between Next.js BFF and FastAPI (Strictly from environment)
    INTERNAL_API_KEY: str
    CRON_SECRET: str = "manna_books_cron_secret_prod"

    # Session & Cookie
    COOKIE_NAME: str = "kenya-nzuri-kabisa"
    COOKIE_DURATION_DAYS: int = 30

    # Business Timezone
    TZ: str = "Africa/Nairobi"

    # KRA eTIMS Gateway & Global System Kill-Switch
    ENABLE_KRA_ETIMS_GLOBAL: bool = False  # Master on/off switch for the entire platform
    KRA_ETIMS_MOCK_MODE: bool = False  # Allows mock simulation during local testing/staging
    KRA_ETIMS_ENV: Literal["SANDBOX", "PRODUCTION"] = "SANDBOX"
    KRA_ETIMS_BASE_URL: str = "https://timsvscu.kra.go.ke/etims-api"
    KRA_ETIMS_AES_ENCRYPTION_KEY: str  # 32 bytes master key strictly from environment

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
