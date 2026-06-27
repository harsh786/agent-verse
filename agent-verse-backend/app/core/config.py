"""Typed application settings, loaded from the environment (12-factor).

Sensitive values (DB password, Redis password, vault master key) are resolved through
:func:`app.core.secrets.read_secret` so the same image works in dev (env vars) and prod
(mounted secret files). Non-sensitive config is loaded directly by pydantic-settings.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    """Application-wide configuration."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- app ---
    app_name: str = "AgentVerse"
    environment: Environment = "development"
    debug: bool = False
    log_level: str = "INFO"

    # --- networking / security ---
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # --- infrastructure DSNs ---
    database_url: str = "postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse"
    redis_url: str = "redis://localhost:6379/0"

    # --- database pool ---
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_timeout: float = 30.0
    db_pool_recycle: int = 1800
    db_pool_pre_ping: bool = True

    # --- observability ---
    service_name: str = "agentverse-backend"
    otel_exporter_otlp_endpoint: str | None = None
    metrics_enabled: bool = True

    # --- LLM (default provider) ---
    default_llm_provider: str = "anthropic"

    # --- feature flags ---
    civilization_enabled: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv_origins(cls, value: object) -> object:
        """Allow CORS origins as a comma-separated string in the environment."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process-wide settings singleton."""
    settings = Settings()
    # Warn loudly if production is using the default database password
    if settings.environment == "production":
        import logging

        if "agentverse:agentverse@" in settings.database_url:
            logging.getLogger(__name__).error(
                "SECURITY: DATABASE_URL contains default password 'agentverse'. "
                "This must be changed before production deployment!"
            )
    return settings
