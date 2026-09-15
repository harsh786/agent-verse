"""Typed application settings, loaded from the environment (12-factor).

Sensitive values (DB password, Redis password, vault master key) are resolved through
:func:`app.core.secrets.read_secret` so the same image works in dev (env vars) and prod
(mounted secret files). Non-sensitive config is loaded directly by pydantic-settings.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
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

    # --- triggers ---
    # DB_ROW_CHANGE trigger: comma-separated allowlist of tables safe to poll.
    # Empty (default) → DB_ROW_CHANGE triggers never fire (fail-closed). Each name
    # must be a bare identifier and is checked against this list before querying,
    # so a tenant-supplied db_table can never inject SQL or read an off-limits table.
    db_row_change_tables: str = ""

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
    # Postgres reclaims a connection left "idle in transaction" past this many ms.
    # Custom Starlette BaseHTTPMiddleware cancels the request task on client
    # disconnect (SSE, polling, navigation) without always rolling back the DB
    # session, leaking a pooled connection stuck idle-in-transaction; without a
    # server-side timeout these accumulate until the pool exhausts and requests
    # hang for db_pool_timeout — the intermittent "blip". 0 disables.
    db_idle_in_transaction_timeout_ms: int = 30_000
    # Safety cap on any single statement so a hung query can't hold a connection
    # indefinitely. Generous so legitimate heavy analytics / vector scans aren't
    # killed. 0 disables.
    db_statement_timeout_ms: int = 120_000
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

    # --- On-prem model cluster (self-hosted vLLM, OpenAI-compatible) ----------
    # A LAN cluster serving several models on separate ports, routed per purpose by
    # the model router: a capable model for planning/execution and a small/fast one
    # for verification, plus dedicated embedding + rerank services. When enabled the
    # app resolves a model→endpoint dispatching provider and auto-wires the embedder
    # and hosted reranker to the cluster (unless those are explicitly set elsewhere).
    onprem_enabled: bool = False
    onprem_api_key: str = "EMPTY"  # vLLM ignores auth; the OpenAI client needs non-empty
    onprem_qwen_base_url: str = ""  # reasoning/planning, e.g. http://192.168.63.104:30080/v1
    onprem_qwen_model: str = "Qwen/Qwen3.5-4B"
    onprem_gemma_base_url: str = ""  # fast/cheap verification, e.g. http://…:30081/v1
    onprem_gemma_model: str = "google/gemma-4-E2B"
    onprem_embedding_base_url: str = ""  # e.g. http://…:30082/v1
    onprem_embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    onprem_embedding_dim: int = 1024  # Qwen3-Embedding-0.6B → 1024-d
    onprem_reranker_url: str = ""  # e.g. http://…:30083/v1/rerank
    onprem_reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"

    # --- NVIDIA NIM (cloud or self-hosted) -----------------------------------
    # When an NVIDIA key is set, NVIDIA is the TOP model: it serves planning and is
    # the fallback for every role. Combined with the on-prem cluster it joins the
    # same model→endpoint router so NVIDIA + Qwen + Gemma are all selectable per task.
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "nvidia/llama-3.1-nemotron-70b-instruct"
    # Optional NVIDIA embedding model (used as the embedder when set); dim must
    # match the pgvector column dim (nvidia/nemotron-3-embed-1b → 2048).
    nvidia_embed_model: str = ""
    nvidia_embed_dim: int = 2048
    # Keep fast interactive chat/execution on the local Qwen while NVIDIA handles
    # planning + fallback (top-tier reasoning). Off → NVIDIA is also the chat default.
    onprem_qwen_is_chat_default: bool = True
    # Suppress the on-prem vLLM reasoning models' chain-of-thought at the server via
    # chat_template_kwargs.enable_thinking=false (Qwen3), so chat gets a clean, fast
    # final answer instead of a long "Thinking Process" dump.
    onprem_disable_thinking: bool = True

    # --- Ollama local inference -----------------------------------------------
    ollama_base_url: str = ""  # e.g. http://localhost:11434
    ollama_default_model: str = "qwen3.8:latest"
    ollama_embed_model: str = "qwen3-embedding:latest"
    ollama_ocr_model: str = "glm-ocr:latest"
    # Dedicated embedding endpoint (OpenAI-compatible /v1/embeddings), separate
    # from the chat LLM base_url — e.g. a self-hosted Qwen3-Embedding on vLLM.
    # When set, the embedder targets this endpoint/model instead of the chat one.
    embedding_base_url: str = ""  # e.g. http://host:30082/v1
    embedding_model: str = ""  # e.g. Qwen/Qwen3-Embedding-0.6B
    embedding_api_key: str = ""  # optional; many self-hosted servers ignore it
    ollama_auto_pull: bool = False

    # --- Embedding vector dimension (must match the embed model) --------------
    embedding_dim: int = 2048  # qwen3-embedding uses 2048-d vectors
    # Embedding quantization for stored/compared vectors: none|int8|binary.
    # int8 = 4x smaller (close accuracy); binary = 32x smaller (coarse, good as a
    # first-stage filter). ``none`` keeps full precision (default). Consumers opt
    # in (e.g. the ingestion near-duplicate pass) — nothing is quantized globally.
    embedding_quantization: str = "none"
    # Binary first-stage retrieval: use the pgvector binary_quantize() Hamming
    # index (migration 0120) to shortlist candidates cheaply, then rerank the
    # shortlist by full-precision cosine. Off by default (exact search unchanged).
    rag_binary_prefilter_enabled: bool = False
    rag_binary_prefilter_shortlist: int = 200  # candidates the Hamming stage keeps

    # --- RAG default-path reranking (WS-10) -----------------------------------
    # Engage a reranking STAGE on the DEFAULT hybrid retrieval path (not only on
    # explicit pattern branches). Uses the one RerankPolicy registry. ``auto``
    # prefers the cross-encoder when its model is available and degrades to a
    # deterministic score-sort otherwise; the stage is an honest passthrough when
    # disabled or when the reranker backend is unavailable.
    rag_default_rerank_enabled: bool = True
    rag_default_rerank_strategy: str = "auto"  # score|rrf|diversity|cross_encoder|llm|hosted|auto
    # --- Hosted reranker (first-class managed reranking provider) --------------
    # A managed cross-encoder rerank API (Cohere-compatible ``/v1/rerank`` shape:
    # Cohere, Voyage, Jina, or a self-hosted equivalent). When a URL is set the
    # ``hosted`` rerank strategy calls it over HTTPS (SSRF-guarded, Bearer auth);
    # unset/erroring, the stage degrades honestly to the local path. No vendor
    # lock-in — any endpoint returning ``{"results":[{"index","relevance_score"}]}``.
    rag_hosted_reranker_url: str = ""
    rag_hosted_reranker_api_key: str = ""
    rag_hosted_reranker_model: str = "rerank-english-v3.0"
    rag_hosted_reranker_timeout_seconds: float = 10.0
    # Allow the hosted reranker to target a private/internal host (e.g. a
    # self-hosted reranker on a LAN IP). Off by default → the SSRF guard blocks
    # RFC-1918/loopback. Set true ONLY for a trusted, operator-configured endpoint.
    rag_hosted_reranker_allow_internal: bool = False
    # Calibrated retrieval confidence below this [0,1] threshold flags a result as
    # low-confidence and (when the fallback is on) triggers a real widening retry.
    rag_low_confidence_threshold: float = 0.35
    rag_low_confidence_fallback_enabled: bool = True
    rag_low_confidence_widen_factor: int = 4  # widen candidate pool by this multiple

    # Exact-text embedding cache in the RAG embed path. Off by default because a
    # cache hit legitimately spends no embedding budget, which changes budget
    # accounting; enable per deployment to cut repeat-embed latency/cost.
    rag_embedding_cache_enabled: bool = False

    # Grantex governance: when True, every agent tool call must pass a covering,
    # active, unrevoked grant (fail-closed). Default off so it is opt-in per
    # deployment — enable once grants are being issued for agents.
    enforce_agent_grants: bool = False

    # Use the richer GroundingPolicy (per-claim scoring + embedding paraphrase tier
    # + calibrated abstention) at the executor grounding checkpoint instead of the
    # baseline substring/typed check. Off by default (behaviour-changing); the
    # baseline already uses T1 typed normalization.
    grounding_policy_enabled: bool = False

    # --- Eval scoring (config-driven; NOTHING hardcoded in the scorer) --------
    # The 7-dimension eval scorer (app/intelligence/eval_runner.py) and the
    # self-improvement decision surfaces read every weight/threshold/budget from
    # here. Defaults reproduce the historically shipped behaviour, so tuning is a
    # config change, not a code change. See app/evals/scoring_config.py.
    eval_pass_threshold: float = 0.70  # average score >= this passes
    # efficiency dimension
    eval_max_iterations_budget: float = 15.0  # iterations budget before efficiency decays
    eval_cost_budget_usd: float = 2.0  # LLM cost at which cost-efficiency hits 0
    eval_efficiency_iter_weight: float = 0.7  # iteration vs cost blend (must sum to 1.0)
    eval_efficiency_cost_weight: float = 0.3
    # accuracy dimension
    eval_accuracy_partial_credit: float = 0.5  # credit when feedback says "partial"
    # safety dimension
    eval_safety_violation_penalty: float = 0.25  # score drop per DENY/blocked event
    # coherence dimension
    eval_coherence_output_weight: float = 0.6  # output-rate vs step-diversity blend
    eval_coherence_diversity_weight: float = 0.4
    # sla dimension
    eval_sla_budget_seconds: float = 300.0  # default wall-clock budget per goal
    eval_sla_iteration_seconds: float = 20.0  # per-iteration time proxy when no timing
    # tool_relevance dimension
    eval_tool_calls_per_step_target: float = 2.0  # ideal tool calls per step
    eval_tool_efficiency_tolerance: float = 5.0  # calls-over-target that zeroes efficiency
    eval_tool_relevance_success_weight: float = 0.6  # success-rate vs efficiency blend
    eval_tool_relevance_efficiency_weight: float = 0.4
    # neutral defaults when evidence is missing
    eval_neutral_no_data_score: float = 0.5  # no step data at all
    eval_neutral_no_tool_calls_score: float = 0.7  # steps exist but made no tool calls
    # self-improvement decision floors (shared by both decision surfaces)
    eval_improve_rag_quality_floor: float = 0.5
    eval_improve_retrieval_confidence_floor: float = 0.4
    eval_improve_goal_success_floor: float = 0.7
    eval_improve_tool_success_floor: float = 0.5
    eval_improve_tool_success_critical: float = 0.3
    eval_improve_cost_efficiency_floor: float = 0.3
    eval_improve_latency_floor: float = 0.3
    eval_improve_regression_case_floor: float = 0.4

    # --- Agent multi-agent auto-selection (WS-10) -----------------------------
    # Default-off safety gate for the advanced multi-agent tier: when on, a goal's
    # complexity/domain/risk can auto-route it to the in-graph supervisor /debate
    # nodes (per-agent enable_* flags remain an explicit override that always wins).
    # The distributed autonomous tier stays governed by ``coordination_ready``.
    agent_auto_multi_agent_enabled: bool = False

    # --- default model names per task type (override via env vars) ---
    # Empty = use the resolved provider's configured model (no hardcoded slug).
    default_planning_model: str = ""
    default_planning_provider: str = ""
    default_execution_model: str = ""
    default_execution_provider: str = ""
    default_verification_model: str = ""
    default_verification_provider: str = ""
    default_summarization_model: str = ""
    default_summarization_provider: str = ""
    default_classification_model: str = ""
    default_classification_provider: str = ""

    # --- Voice OS configuration ---------------------------------------------------
    # --- Public URL (for magic links in approval notifications) ---
    public_base_url: str = "http://localhost:5173"

    # Public base URL of THIS backend (for building externally-callable workflow
    # webhook trigger URLs). Empty → publish() returns only the relative path.
    workflow_webhook_base_url: str = ""

    # HMAC signing key for stateless workflow webhook trigger tokens. Empty in
    # dev falls back to a warned default; set a real secret in any deployment.
    workflow_webhook_secret: str = ""

    # --- Voice OS configuration ---
    voice_enabled: bool = True
    voice_device: str = "cpu"  # "cpu" | "cuda"
    voice_stt_provider: str = "faster_whisper"
    voice_tts_provider: str = "kokoro"  # kokoro | omnivoice | elevenlabs | openai_tts | azure_tts
    voice_stt_model: str = "large-v3-turbo"
    voice_tts_model: str = "k2-fsa/OmniVoice"
    model_cache_dir: str = "/app/models"
    voice_persona_bucket: str = "agentverse-voice-personas"
    voice_greeting_cache_ttl: int = 300
    voice_max_audio_mb: int = 25
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    # --- feature flags ---
    civilization_enabled: bool = False

    # --- isolated agent execution environment ---
    # Master switch: route agent execution through the isolated execution plane.
    # Default off — existing behavior is fully preserved when this is False.
    isolated_agent_execution: bool = False
    # Hard requirement: if True AND no runner is available/healthy, fail closed.
    # If False, fall back to in-process execution when the runner is unavailable.
    isolated_execution_required: bool = False
    # Runner back-ends (both default off; at most one should be True at a time)
    isolated_execution_local_runner: bool = False  # subprocess runner
    isolated_execution_kubernetes_runner: bool = False  # Kubernetes Job runner

    # --- Trigger consumers (WT-4) ---
    # Master switch: start the long-running trigger consumers (chain/HITL/memory)
    # on app startup. Default on — consumers self-disable when Redis is absent.
    triggers_consumers_enabled: bool = True
    # Gate the extended trigger families (data/monitoring/iot/advanced). These
    # require external clients (MQTT/S3/etc.) that are not wired by default, so
    # they stay off unless explicitly enabled.
    triggers_extended_consumers_enabled: bool = False

    # Advanced RAG pattern feature flags
    enable_raptor: bool = True
    enable_flare: bool = True
    enable_self_rag: bool = True
    enable_speculative_rag: bool = True
    enable_colbert: bool = True
    enable_tree_of_thoughts: bool = True
    enable_self_consistency: bool = True
    enable_peer_review: bool = True
    enable_agentic_chunking: bool = True
    colbert_checkpoint: str = "colbert-ir/colbertv2.0"

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
    repo_ingest_max_files: int = Field(default=200, ge=1, le=1000)
    repo_ingest_max_file_bytes: int = Field(default=1_048_576, ge=1024)
    repo_ingest_max_total_bytes: int = Field(default=20_971_520, ge=1024)
    repo_ingest_max_repository_bytes: int = Field(default=104_857_600, ge=1024)
    repo_ingest_max_repository_files: int = Field(default=10_000, ge=1)
    repo_ingest_clone_timeout_seconds: int = Field(default=120, ge=1, le=600)
    repo_ingest_stale_job_seconds: int = Field(default=900, ge=60)
    repo_ingest_lease_seconds: int = Field(default=60, ge=10, le=600)
    repo_ingest_heartbeat_seconds: int = Field(default=5, ge=1, le=60)
    repo_ingest_ca_bundle: str = ""

    # --- search ---
    searxng_url: str = "http://searxng:8080"
    web_search_allowed_domains: str = ""

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
    siem_type: str = ""  # "splunk" | "elasticsearch" | "datadog" | "cef" | "leef" | "webhook"
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
    seller_gstin: str = "27AAAAA0000A1Z5"  # placeholder — override in production
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

    @model_validator(mode="after")
    def _validate_repository_lease_margin(self) -> Settings:
        if self.repo_ingest_heartbeat_seconds * 3 > self.repo_ingest_lease_seconds:
            raise ValueError(
                "repo_ingest_heartbeat_seconds must be at most one-third of lease duration"
            )
        return self

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
