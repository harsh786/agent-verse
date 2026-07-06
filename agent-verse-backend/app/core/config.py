"""Typed application settings, loaded from the environment (12-factor).

Sensitive values (DB password, Redis password, vault master key) are resolved through
:func:`app.core.secrets.read_secret` so the same image works in dev (env vars) and prod
(mounted secret files). Non-sensitive config is loaded directly by pydantic-settings.
"""

from __future__ import annotations

import os
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

    # --- Redis HA settings ---
    # Sentinel (recommended for <50 k goals/day):
    #   REDIS_SENTINEL_URLS=sentinel1:26379,sentinel2:26379,sentinel3:26379
    redis_sentinel_urls: str = ""
    redis_sentinel_master: str = "mymaster"
    # Cluster (high-throughput / horizontal sharding):
    #   REDIS_CLUSTER_NODES=node1:6379,node2:6379,node3:6379
    redis_cluster_nodes: str = ""
    # Shared Redis password (used by Sentinel master, Cluster, and single-node)
    redis_password: str = ""

    # --- database pool ---
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_timeout: float = 30.0
    db_pool_recycle: int = 1800
    db_pool_pre_ping: bool = True
    db_pool_max: int = Field(default=20, description="Max asyncpg pool connections")
    db_pool_min: int = Field(default=5, description="Min asyncpg pool connections")
    redis_max_connections: int = Field(default=50, description="Max Redis pool connections")
    http_max_connections: int = Field(default=100, description="Max HTTP connection pool size")
    http_keepalive_connections: int = Field(default=20, description="HTTP keepalive connections")

    # --- observability ---
    service_name: str = "agentverse-backend"
    otel_exporter_otlp_endpoint: str | None = None
    metrics_enabled: bool = True

    # --- LLM (default provider) ---
    default_llm_provider: str = "anthropic"
    default_model: str = ""  # Canonical default model slug; empty = provider default
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    voyage_api_key: str = ""

    # --- feature flags ---
    civilization_enabled: bool = False

    # --- Agent Civilization ---
    civilization_max_agents_per_tenant: int = 50
    civilization_max_spawn_depth: int = 5
    civilization_default_budget_usd: float = 10.0
    civilization_tick_interval_seconds: int = 30

    # --- SSO / Keycloak ---
    frontend_url: str = "http://localhost:5173"
    sso_enabled: bool = False
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "agentverse"
    keycloak_client_id: str = "agentverse-backend"
    keycloak_client_secret: str = ""  # Empty = dev mode; required in production with SSO

    # --- email (SMTP) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_tls: bool = True

    # --- object storage (MinIO / S3) ---
    minio_endpoint: str = "http://minio:9000"
    minio_access_key: str = "agentverse"
    minio_secret_key: str = "agentverse_minio"

    # --- tools ---
    allow_shell_exec: bool = False
    allow_subprocess_exec: bool = False

    # --- search ---
    searxng_url: str = "http://searxng:8081"

    # --- SAML 2.0 ---
    saml_enabled: bool = False
    saml_idp_metadata_url: str = ""
    saml_entity_id: str = "agentverse"
    saml_acs_url: str = ""  # Assertion Consumer Service URL
    saml_name_id_format: str = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"

    # --- security / MFA ---
    mfa_enforcement_enabled: bool = Field(
        default=False, description="Enforce MFA for mfa_enabled users"
    )

    # --- SIEM Integration ---
    siem_type: str = ""   # "splunk" | "elasticsearch" | "datadog" | "cef" | "leef" | "webhook"
    siem_endpoint: str = ""
    siem_token: str = ""
    siem_api_key: str = ""

    # --- scope enforcement ---
    scope_enforcement_legacy_allow: bool = False

    # --- billing (Stripe) ---
    stripe_api_key: str = ""

    # --- billing details (Stripe) ---
    stripe_price_starter: str = ""
    stripe_price_professional: str = ""
    stripe_price_enterprise: str = ""
    stripe_success_url: str = "https://app.agentverse.ai/settings/billing?success=1"
    stripe_cancel_url: str = "https://app.agentverse.ai/settings/billing?cancelled=1"

    # --- billing (Razorpay) ---
    razorpay_key_id: str = "rzp_test_placeholder"
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    allow_mock_payments: bool = Field(
        default=False,
        description=(
            "Allow payment verification without real Razorpay credentials. "
            "MUST be False in production. Set True only in development/testing."
        ),
    )
    inr_to_usd_rate: float = Field(
        default=83.0,
        description="INR to USD conversion rate. Update when rate changes significantly.",
    )

    # --- India compliance (DPDP/GST) ---
    seller_gstin: str = "27AAAAA0000A1Z5"   # placeholder — override in production
    seller_name: str = "AgentVerse Technologies Pvt Ltd"
    dpo_name: str = "Data Protection Officer"
    dpo_email: str = "dpo@agentverse.ai"

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

    @property
    def is_sso_production_safe(self) -> bool:
        """True if SSO is disabled OR a non-default client secret is set."""
        if not self.sso_enabled:
            return True
        secret = self.keycloak_client_secret
        return bool(secret) and secret != "agentverse-dev-secret"


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
        if settings.sso_enabled and not settings.is_sso_production_safe:
            logging.getLogger(__name__).error(
                "SECURITY: KEYCLOAK_CLIENT_SECRET is empty or set to default. "
                "Set a strong secret before production SSO deployment!"
            )
    return settings


def get_provider_env(name: str) -> str:
    """Return provider secret from process env, falling back to typed settings."""
    value = os.getenv(name, "")
    if value:
        return value
    setting_name = name.lower()
    try:
        return str(getattr(get_settings(), setting_name, "") or "")
    except Exception:
        return ""
