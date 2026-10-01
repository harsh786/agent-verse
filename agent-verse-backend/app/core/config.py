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
    # Comma-separated IPs/CIDRs of the reverse proxies / load balancers whose
    # X-Forwarded-For (or X-Real-IP) may be believed, e.g. the ingress
    # controller's pod CIDR. Empty (default) = trust NO proxy: the client IP is
    # the TCP peer. Never list a range that untrusted workloads can originate
    # from — any host in it can then claim any source IP (IP allowlists, rate
    # limits). The client IP is the right-most XFF hop not in this list.
    trusted_proxies: str = ""

    # --- triggers ---
    # DB_ROW_CHANGE trigger: comma-separated allowlist of tables safe to poll.
    # Empty (default) → DB_ROW_CHANGE triggers never fire (fail-closed). Each name
    # must be a bare identifier and is checked against this list before querying,
    # so a tenant-supplied db_table can never inject SQL or read an off-limits table.
    db_row_change_tables: str = ""
    # FILE_DROP trigger: the directory tenant drop folders live under. A trigger's
    # file_drop_path is a folder RELATIVE to <file_drop_root>/<tenant_id> and is
    # confined to it. Empty (default) → file_drop triggers are disabled.
    file_drop_root: str = ""

    # --- infrastructure DSNs ---
    database_url: str = "postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse"
    # Separate DSN for cross-tenant system work (beat scanners, retention and
    # partition maintenance, startup warm-up). Production runs the API as a
    # NOBYPASSRLS role so every request is tenant-isolated by the database; the
    # few legitimate cross-tenant jobs use this BYPASSRLS maintenance role via
    # get_system_session_factory(). Empty = same as database_url (dev/superuser).
    maintenance_database_url: str = ""
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
    # Opt-in: FastAPI ASGI server-span instrumentation. Off by default because
    # opentelemetry-instrumentation-fastapi crashes per-request on this app's
    # nested/included router structure (500s the CORS preflight). Library
    # instrumentors + manual spans stay on regardless.
    otel_instrument_fastapi: bool = False
    # Attach LLM prompt/completion content to gen_ai spans (redacted). Off by default.
    otel_capture_llm_content: bool = False
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
    # Local sentence-transformers embedding model (lowest-priority embedder), e.g.
    # all-mpnet-base-v2 (768-d). A typed field so a value set only in .env is seen:
    # pydantic reads .env without exporting it to os.environ.
    sentence_transformers_model: str = ""
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
    # shortlist by full-precision cosine. Wired into engine.hybrid_search's vector
    # leg for collections at/above the threshold; on any error (pgvector < 0.7,
    # missing 0120 index, halfvec dims) it falls back to exact search, so enabling
    # it can never degrade results — only trade a little recall for latency on very
    # large collections. Off by default until recall is benchmarked per deployment.
    rag_binary_prefilter_enabled: bool = False
    rag_binary_prefilter_shortlist: int = 200  # candidates the Hamming stage keeps
    rag_binary_prefilter_threshold: int = 50_000  # min collection chunks to prefilter

    # --- RAG default-path reranking (WS-10) -----------------------------------
    # Engage a reranking STAGE on the DEFAULT hybrid retrieval path (not only on
    # explicit pattern branches). Uses the one RerankPolicy registry. ``auto``
    # prefers the cross-encoder when its model is available and degrades to a
    # deterministic score-sort otherwise; the stage is an honest passthrough when
    # disabled or when the reranker backend is unavailable.
    rag_default_rerank_enabled: bool = True
    rag_default_rerank_strategy: str = "auto"  # score|rrf|diversity|cross_encoder|llm|hosted|auto
    # RERANK-PRELOAD: warm the cross-encoder in the background at API startup and
    # in each Celery worker process (only when the strategy above uses it). Until
    # it is warm a search waits at most ``rag_rerank_warmup_wait_seconds`` for it,
    # then skips the cross-encoder and flags ``rerank_skipped`` on each citation.
    rag_rerank_preload: bool = True
    rag_rerank_warmup_wait_seconds: float = 2.0
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

    # Exact-text embedding cache in the RAG embed path (first-class, on by
    # default). A cache hit skips the provider call and its token cost, but STILL
    # consumes shared budget (see _BudgetedEmbedder.embed) so a fan-out is denied
    # even when its query embedding is cached — budget enforcement is never
    # bypassed. Cuts repeat-embed latency/cost across collections and requests.
    rag_embedding_cache_enabled: bool = True

    # Grantex governance: when True, every agent tool call must pass a covering,
    # active, unrevoked grant (fail-closed). Default ON (secure by default);
    # set ENFORCE_AGENT_GRANTS=false only for a deployment that issues no grants.
    enforce_agent_grants: bool = True

    # Use the richer GroundingPolicy (per-claim scoring + embedding paraphrase tier
    # + calibrated abstention) at the executor grounding checkpoint instead of the
    # baseline substring/typed check. Off by default (behaviour-changing); the
    # baseline already uses T1 typed normalization.
    grounding_policy_enabled: bool = True

    # --- Agent runtime trade-off switches (defaults = shipped behaviour) -------
    # An errored guardrail engine fails CLOSED only on high-risk steps by default
    # (low-risk steps proceed, logged). true = fail closed on every step.
    guardrail_fail_closed_all: bool = False
    # Risk class of declared-"high" interactive RPA tools (rpa_click, rpa_type,
    # rpa_select_option ...): write_low (no approval) or write_high (approval-gated
    # like rpa_submit_form). Read-only RPA tools are unaffected.
    rpa_interaction_risk: Literal["write_low", "write_high"] = "write_low"
    # How often a model with no capability data probes richer execution modes
    # (structured plans / parallel tool calls) before enough data exists. 0..1.
    explore_rate_unknown: float = Field(default=1.0, ge=0.0, le=1.0)

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
    # First-class advanced multi-agent tier (default on): a goal's
    # complexity/domain/risk can auto-route it to the in-graph supervisor /debate
    # nodes (per-agent enable_* flags remain an explicit override that always wins).
    # Only goals the PatternSelector deems multi-agent-worthy fan out — simple
    # goals stay single-agent — and per-goal/tenant cost budgets bound the spend.
    # The distributed autonomous tier stays governed by ``coordination_ready``.
    agent_auto_multi_agent_enabled: bool = True
    # Tier-2 goal classification: for MEDIUM-complexity goals the fast keyword
    # classifier is unsure about (confidence <= 0.85), ask the platform LLM to
    # classify. Off by default (adds ~200ms + one LLM call per such goal); the
    # heuristic stays the fallback whenever the LLM call fails or is unparseable.
    orchestration_llm_classifier_enabled: bool = False

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
    # Root under which each tenant's DuckDB databases / data files must live
    # (``<root>/<tenant_id>/``). The DuckDB ingestion connector refuses any
    # path outside the tenant's directory and disables DuckDB external access
    # beyond it, so tenant SQL can never read API/worker host files.
    duckdb_data_root: str = "/var/lib/agentverse/duckdb"
    # DuckDB executes tenant SQL in-process on the API/worker host; even
    # confined, it is off unless an operator enables it explicitly.
    ingestion_connector_duckdb_enabled: bool = False
    # Hard cap on a single synchronous knowledge upload (/knowledge/ingest/file,
    # /pdf, /docx). The body used to be read whole into memory with no limit.
    knowledge_max_upload_bytes: int = 50 * 1024 * 1024
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

    # --- Trigger event bus (TRG-18, app/triggers/bus.py) ---
    # One Redis Stream per event family; consumers read them through consumer
    # groups so an event published while they are down is delivered later.
    trigger_bus_stream_goal: str = "trigger:stream:goal"  # goal.completed/failed/score_below
    trigger_bus_stream_hitl: str = "trigger:stream:hitl"  # hitl.approved/rejected
    trigger_bus_stream_memory: str = "trigger:stream:memory"  # memory.created
    trigger_bus_stream_event: str = "trigger:stream:event"  # trigger:event:* (custom/chat/state)
    # Approximate MAXLEN cap per stream (XADD MAXLEN ~ N).
    trigger_bus_stream_maxlen: int = 100_000
    # Also PUBLISH to the legacy pub/sub channel so replicas still running the
    # pub/sub consumers during a rolling deploy keep receiving events. Turn off
    # once every replica consumes the streams (rollout in app/triggers/bus.py).
    trigger_bus_dual_publish: bool = True
    # Consumer tuning: XREADGROUP block, batch size, the idle time after which a
    # pending (delivered, never acked) entry is XAUTOCLAIMed by another consumer,
    # and the delivery count after which a poison entry is acked and dropped.
    trigger_bus_block_ms: int = 5_000
    trigger_bus_read_count: int = 50
    trigger_bus_claim_idle_ms: int = 60_000
    trigger_bus_max_deliveries: int = 10

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
    # Download the ColBERT checkpoint in the background at startup when it is not
    # cached (the strategy is only offered once the checkpoint is local).
    colbert_prefetch: bool = True

    # --- RAFT (retrieval-augmented fine-tuning) ---
    # Cap on curated chunks read into one training dataset (keyset-paged).
    raft_max_training_chunks: int = Field(default=2000, ge=1, le=100_000)
    # KB-32: RAFT on any vendor exposing OpenAI's fine-tuning REST API (files +
    # fine_tuning/jobs) and an OpenAI-compatible chat endpoint to serve the model.
    raft_compat_fine_tune_base_url: str = ""
    raft_compat_fine_tune_api_key: str = ""
    raft_compat_fine_tune_provider_id: str = "openai_compatible"
    raft_compat_fine_tune_usd_per_example: str = "0.008"
    raft_compat_fine_tune_allow_internal: bool = False
    raft_chunk_page_size: int = Field(default=500, ge=1, le=10_000)
    # Held-out examples scored (one inference call each) by POST .../evaluate.
    raft_max_eval_examples: int = Field(default=50, ge=1, le=1000)
    # In-flight jobs advanced per beat tick by the status poller.
    raft_poll_batch_size: int = Field(default=50, ge=1, le=1000)

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
    # Repository ingestions one tenant may have queued or running at once
    # (each is a clone of up to repo_ingest_max_repository_bytes on a worker).
    repo_ingest_max_concurrent_per_tenant: int = Field(default=2, ge=1, le=50)

    # --- search ---
    searxng_url: str = "http://searxng:8080"
    web_search_allowed_domains: str = ""

    # --- ingestion egress (SSRF policy for tenant-configured source hosts) -----
    # Connectors fetch hosts the tenant supplies (Jira/Confluence base_url, a
    # self-hosted GitLab, a ServiceNow instance, an Elasticsearch url, web-crawl
    # seed_urls). Whatever is fetched is indexed into that tenant's own
    # knowledge collection, so an unguarded fetch of 169.254.169.254 hands them
    # the platform's cloud credentials. Blocked by default.
    #
    # An on-prem deployment whose Jira really does live on a LAN turns this on
    # AND names the hosts. Both are operator/env-only by design: an allowlist
    # alone punches no hole, and nothing a tenant can put in connection_config
    # reaches either setting.
    ingestion_allow_internal_sources: bool = False
    ingestion_internal_source_allowlist: str = ""  # comma-separated hostnames
    # Connector drivers are pinned to the addresses the egress check validated
    # (no DNS-rebinding window). A driver that resolves hosts outside Python and
    # cannot be pinned (confluent-kafka / librdkafka) is refused while this is on.
    ingestion_egress_strict_pinning: bool = True

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
