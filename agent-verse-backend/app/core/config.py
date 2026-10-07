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

    # --- LLM SDK clients (PROV-06) ---
    # Every vendor SDK client is built with these instead of the SDK default
    # (600 s), so no caller can hang for ten minutes on a stuck endpoint.
    llm_client_timeout_seconds: float = 300.0
    llm_client_max_retries: int = 2
    # Provider throttling (HTTP 429) on top of the SDK's own short retries (P5-1):
    # exponential backoff with jitter, Retry-After honoured, the total wait capped
    # here AND by the goal's remaining time budget. 429s never trip the breaker.
    llm_rate_limit_max_retries: int = Field(default=4, ge=0, le=20)
    llm_rate_limit_base_delay_seconds: float = Field(default=1.0, gt=0.0)
    llm_rate_limit_max_delay_seconds: float = Field(default=30.0, gt=0.0)
    llm_rate_limit_max_total_wait_seconds: float = Field(default=120.0, ge=0.0)

    # --- goal / step watchdog (GOAL-STALL) ---
    # A step that has not finished after this much ACTIVE time (time spent waiting
    # for a human approval does not count) is cancelled and recorded as failed, so
    # the verify/replan path runs instead of the goal sitting silent. A provider
    # call alone may take llm_client_timeout_seconds x (1 + retries).
    agent_step_timeout_seconds: float = Field(default=900.0, ge=1.0)
    # While a step runs, a ``step_heartbeat`` event is emitted at this interval.
    agent_step_heartbeat_seconds: float = Field(default=30.0, ge=0.1)
    # A worker running a goal writes goals.heartbeat_at at this interval ...
    goal_heartbeat_interval_seconds: float = Field(default=15.0, ge=0.1)
    # ... and the beat reaper treats a goal whose heartbeat is older than this as
    # orphaned (its runner died): requeued once if it never ran a tool, else failed.
    goal_heartbeat_stale_seconds: float = Field(default=120.0, ge=1.0)
    # The heartbeat stops when the goal's event loop has not made progress for
    # this long (a wedged worker), so the reaper takes the goal over.
    goal_loop_stall_seconds: float = Field(default=600.0, ge=1.0)
    goal_watchdog_max_requeues: int = Field(default=1, ge=0, le=5)
    # A step that finds the tenant's executor bulkhead full waits up to this long
    # for a slot (backoff + jitter) before it is refused (a08-F199-02).
    tenant_bulkhead_wait_seconds: float = Field(default=10.0, ge=0.0, le=600.0)

    # --- dead-worker message restore (a06-F099-03) ---
    # The Redis broker redelivers an unacked message only after its one
    # visibility timeout (~25 h, sized for the longest goal). With this on, each
    # worker records which of its processes holds each message and heart-beats
    # (every grace / 4 s); a beat job puts the messages of a worker whose
    # heartbeat is older than the grace back on their queue at once. The 25 h
    # timeout stays as the backstop (old workers, unrecorded messages).
    celery_dead_worker_restore_enabled: bool = True
    celery_dead_worker_grace_seconds: float = Field(default=120.0, ge=10.0)

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
    # DSN alembic migrates with: the schema OWNER. Empty = database_url (dev /
    # single-role setups). When the API runs as the least-privilege
    # application role (DATABASE_URL), migrations need this owner DSN; after
    # migrating, alembic provisions APP_DB_USER (see app/db/app_role.py).
    migration_database_url: str = ""
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
    # Postgres reclaims a connection left "idle in transaction" past this many ms
    # (backstop; see app.db.session.run_in_fresh_loop for the root-cause fix).
    # NOTE: a PgBouncer in front ignores this startup parameter — set it on the
    # server too. 0 disables.
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
    # score|rrf|diversity|cross_encoder|tfidf|hosted|auto (validated below)
    rag_default_rerank_strategy: str = "auto"
    # RERANK-PRELOAD: warm the cross-encoder in the background at API startup and
    # in each Celery worker process (only when the strategy above uses it). Until
    # it is warm a search waits at most ``rag_rerank_warmup_wait_seconds`` for it,
    # then skips the cross-encoder and flags ``rerank_skipped`` on each citation.
    rag_rerank_preload: bool = True
    rag_rerank_warmup_wait_seconds: float = 2.0
    # RERANK-BOUNDED: the local cross-encoder is CPU-bound and serialised (one
    # model call at a time). Every rerank — async stage or synchronous policy —
    # goes through ONE bounded queue; past these limits a search keeps its
    # retrieval order, flagged ``rerank_skipped: busy|budget_exceeded`` and counted
    # (``agentverse_rerank_degraded_total``), instead of queueing without limit.
    # Time the rerank stage may take (warm-up wait + queue wait + inference); it is
    # also capped by what is left of the retrieval strategy deadline.
    rag_rerank_budget_ms: int = Field(default=2500, ge=0, le=60_000)
    # Rerank jobs allowed to WAIT behind the one running (0 = no waiting at all).
    rag_rerank_max_queue_depth: int = Field(default=4, ge=0, le=256)
    # Only the top-N candidates (retrieval order) are cross-encoded; the rest
    # follow them in retrieval order. 0 = rerank every candidate.
    rag_rerank_max_candidates: int = Field(default=30, ge=0, le=1000)
    # Tokens of (query + passage) the cross-encoder reads (its tokenizer truncates).
    rag_rerank_max_length: int = Field(default=256, ge=32, le=512)
    # torch intra-op threads, set ONCE when the cross-encoder model loads (the
    # setting is process-wide: it also applies to other torch models in the same
    # process). 0 = auto: min(2, CPUs available to the process — affinity and the
    # cgroup CPU quota). torch's default is every CPU of the node, which on a VM
    # shared with Postgres/Redis/workers oversubscribes the CPU and starves the
    # API event loop.
    rag_rerank_torch_threads: int = Field(default=0, ge=0, le=256)
    # torch inter-op threads (also process-wide; torch accepts it only before its
    # first parallel work, so a later attempt is logged and ignored).
    rag_rerank_torch_interop_threads: int = Field(default=1, ge=0, le=64)
    # L-03: Celery prefork children warm the retrieval models (cross-encoder,
    # ColBERT checkpoint download) at start only when their pool opts in. Warm
    # children cost ~450 MB each (torch + sentence-transformers + model); pools
    # that rarely rerank (workflow, sub-goal) load lazily on first use instead.
    worker_preload_retrieval_models: bool = False
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
    # Workflow approval SLA sweep (workflow.check_hitl_escalations): how often the
    # beat runs it — a timed-out approval gets its timeout_action at most this
    # late — and how many overdue approvals one sweep handles (most overdue
    # first; the rest wait for the next tick).
    workflow_hitl_sla_sweep_seconds: float = Field(default=60.0, ge=5.0, le=3600.0)
    workflow_hitl_sla_sweep_batch: int = Field(default=200, ge=1, le=5000)

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
    # Kill switch for the MongoDB ingestion connector (TG-15): false refuses new
    # MongoDB Sources, syncs (failed job with the reason) and health checks.
    ingestion_connector_mongodb_enabled: bool = True
    # Kill switch for the MCP built-in MongoDB connector (NF-13): false refuses
    # new/edited MongoDB connections (422), the connector test and every tool call.
    mcp_connector_mongodb_enabled: bool = True
    # MongoDB ingestion connector bounds (C1 / MDB-12). A server that accepts and
    # then stalls used to block a sync (and the health check) forever. Every
    # wait is bounded: TCP connect, server selection, each socket read, and the
    # server-side time of each query (maxTimeMS, below the socket timeout so a
    # slow query fails with an honest MaxTimeMSExpired). A tenant's ``timeout_ms``
    # may only lower connect/selection; it can never raise or unbound them.
    ingestion_mongodb_connect_timeout_ms: int = 10_000
    ingestion_mongodb_server_selection_timeout_ms: int = 10_000
    ingestion_mongodb_socket_timeout_ms: int = 60_000
    ingestion_mongodb_max_time_ms: int = 30_000
    # TTL of a running sync's per-Source lock (TG-12). The worker renews it every
    # third of the TTL; a worker that dies frees its Source within one TTL.
    ingestion_sync_lock_ttl_seconds: int = 300
    # SYNC-ORPHAN (app/ingestion/orphan_recovery.py): a sync whose worker died
    # (its job heartbeat older than the stale window AND its Source lock
    # expired) is requeued as the same job, resuming from the last checkpoint,
    # with an exponential backoff countdown; after max_attempts runs it is
    # failed with the reason. Recovery bound ~= max(lock TTL, stale) + 60 s.
    ingestion_sync_orphan_recovery_enabled: bool = True
    ingestion_sync_heartbeat_stale_seconds: int = Field(default=300, ge=1)
    ingestion_sync_max_attempts: int = Field(default=3, ge=1, le=20)
    ingestion_sync_requeue_backoff_seconds: int = Field(default=30, ge=0)
    ingestion_sync_requeue_backoff_max_seconds: int = Field(default=600, ge=0)
    # Documents ONE connector sync ingests at once (fetch, parse / OCR, embed,
    # index). The cursor and connector acknowledgements still advance in source
    # order, only past documents that finished. 1 = one document at a time.
    ingestion_sync_doc_concurrency: int = Field(default=4, ge=1, le=64)
    # GET /sources/{id}/health results (failures too) are shared through Redis
    # for this long per Source + connection config (C8); 0 disables the cache.
    ingestion_health_cache_seconds: int = 60
    # Hard cap on a single synchronous knowledge upload (/knowledge/ingest/file,
    # /pdf, /docx). The body used to be read whole into memory with no limit.
    knowledge_max_upload_bytes: int = 50 * 1024 * 1024
    # Where THIS deployment stores tenant data (e.g. "eu-west-1"), as declared by
    # the operator; empty = not declared. Reported by /enterprise/compliance/
    # residency and /regions and used by the GDPR EU-residency control. There is
    # no per-tenant region selection: every tenant shares the deployment's
    # region. a10-F253-01 (these endpoints used to claim us-east-1 / eu-west-1
    # for every tenant of every deployment).
    data_region: str = ""
    data_backup_region: str = ""
    # Hard cap on one OCR document (/ocr/extract upload or decoded base64, each
    # /ocr/batch document, and the agent-callable extract_document tool — the one
    # OCR size limit, app/ocr/limits.py; the tool used to stop at a hardcoded
    # 10 MiB). The request body is also bounded before any route reads it, at
    # one base64-encoded document plus 1 MiB envelope (a /ocr/batch request's
    # documents share that body): app/integrations/body_limit.py. a10-F243-05.
    ocr_max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    # OCR parallelism (OCR-PAR, app/ocr/concurrency.py). Every OCR caller in a
    # process (API requests, ZIP members, ingestion jobs) shares ONE pool:
    # OCR threads AND pages in flight (page bitmaps in memory) per process;
    # 0 = the CPUs this process may use (cgroup quota aware, split between a
    # prefork worker's children).
    ocr_max_concurrency: int = Field(default=0, ge=0, le=256)
    # Pages of ONE document OCR'd at once; 0 = one less than the global cap
    # (min 2 when the cap is 2), so another document still progresses beside a
    # huge scan. Never more than ocr_max_concurrency.
    ocr_page_concurrency: int = Field(default=0, ge=0, le=256)
    # LLM-vision fallback calls in flight per process (low-confidence pages).
    ocr_vision_concurrency: int = Field(default=4, ge=1, le=64)
    # Resolution scanned PDF pages are rasterised at (one page at a time).
    ocr_render_dpi: int = Field(default=300, ge=72, le=600)
    voice_persona_bucket: str = "agentverse-voice-personas"
    voice_greeting_cache_ttl: int = 300
    voice_max_audio_mb: int = 25
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    # --- code sandbox concurrency (app/tools/code_execution.py) ---
    # Concurrent sandbox containers one tenant may run across ALL replicas and
    # workers (Redis lease set), and per process (each holds a dedicated thread).
    code_exec_max_concurrent_per_tenant: int = 4
    code_exec_max_concurrent_per_host: int = 8
    # Remote code-sandbox runner (app/sandbox/runner.py; compose `code-sandbox`,
    # the Helm charts' code-sandbox Deployment). When set, every code execution
    # (workflow code steps, /tools/execute-code, chat) runs there — never in
    # Docker or a host subprocess. e.g. http://code-sandbox:8080
    code_sandbox_url: str = ""
    # Shared secret the runner authenticates every execution request with.
    code_sandbox_token: str = Field(default="", repr=False)

    # --- RPA browser sessions (app/rpa/session_manager.py) ---
    # Live browsers one tenant may hold across ALL replicas/workers (Redis lease set).
    rpa_max_sessions_per_tenant: int = 5
    # Chromium processes one API/worker process may run, all tenants together.
    rpa_max_browsers_per_host: int = 10
    # At a full cap, a session idle this long may be closed to make room (never a
    # recently used one, which may be mid-workflow for another goal).
    rpa_session_evict_idle_s: int = 120
    # Largest upstream response the browser SSRF guard will relay (bytes); larger
    # ones are aborted rather than buffered in the API/worker.
    rpa_max_response_bytes: int = 25 * 1024 * 1024

    # --- perception (app/perception/browser_agent.py, app/api/perception.py) ---
    # Pages one process renders at once on its single shared Chromium.
    perception_max_concurrent_pages: int = 8
    # Pages one tenant may have loading at once across all replicas (Redis leases).
    perception_max_pages_per_tenant: int = 10

    # --- tenant file workspace (/tools/files, app/tools/workspace_store.py) ---
    # Largest single file (UTF-8 bytes); larger writes are 413.
    workspace_max_file_bytes: int = 1024 * 1024
    # Per-tenant totals across all files/directories; past them writes are 507.
    workspace_max_tenant_bytes: int = 100 * 1024 * 1024
    workspace_max_entries: int = 10_000

    # --- platform email relay (POST /tools/email/send) ---
    email_max_recipients: int = 50
    # Recipients per tenant per UTC day; 0 = the plan default (app/tools/email_quota.py).
    email_daily_recipient_quota: int = 0
    # Ports a tenant-owned SMTP sender (PUT /tenants/me/email/smtp) may use. The
    # host passes the SSRF guard; the port list keeps the test endpoint from
    # probing arbitrary services on the hosts that guard allows.
    tenant_smtp_allowed_ports: str = "25,465,587,1025,2525"

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
    # NF-17: a finished training-export file is deleted from object storage this
    # many hours after its job completed (the job becomes 'expired'); 0 keeps
    # files forever. The hourly beat sweep handles at most
    # batch_size * max_batches jobs per run.
    training_export_retention_hours: int = Field(default=168, ge=0)
    training_export_expiry_batch_size: int = Field(default=100, ge=1, le=1000)
    training_export_expiry_max_batches: int = Field(default=10, ge=1)
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

    # --- rate limiting ---
    # API replicas sharing each tenant's per-minute plan limit. Only used while
    # Redis (the shared sliding window) is unreachable: each replica then
    # enforces plan_limit / rate_limit_replica_count locally, so the cluster as
    # a whole stays within the plan limit instead of N x it (RATE-03).
    rate_limit_replica_count: int = Field(default=1, ge=1, le=10_000)

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
    # Owner decision 2026-10-06: ingestion sources (MinIO/S3, HTTP, crawl,
    # repositories), connectors (MongoDB and every database / broker source,
    # MCP / tool connectors, their OAuth token URLs) and model endpoints (tenant
    # LLM base URLs, hosted reranker, fine-tune endpoint) may reach PRIVATE and
    # internal hosts / IPs, in every environment. Cloud metadata, link-local and
    # 0.0.0.0 stay blocked. false restores public-only + the allowlists above.
    allow_private_network_access: bool = True
    # MongoDB (MCP builtin + ingestion): a tenant can never weaken TLS
    # verification (tlsInsecure / tlsAllowInvalid* / ...; MDB-07). This dev-only
    # switch additionally lets a connection run WITHOUT TLS (tls=false /
    # ssl=false). Production ignores it.
    mongodb_allow_non_tls: bool = False
    # MongoDB MCP builtin bounds (MDB-08): documents a find returns without a
    # limit, the most any find / aggregate returns (limit 0 / negative / larger
    # is clamped to it), and the per-call operation timeout (CSOT: every command
    # carries maxTimeMS; a stalled server cannot hold the call longer).
    mongodb_tool_default_limit: int = 100
    mongodb_tool_max_documents: int = 1000
    mongodb_tool_timeout_ms: int = 15000
    # Pooled MongoClients per (tenant, connector, credentials) (C2): bounded LRU,
    # closed after this idle time / total age (hosts are re-checked on rebuild).
    mongodb_client_cache_size: int = 64
    mongodb_client_idle_ttl_s: int = 300
    mongodb_client_max_age_s: int = 1800
    # Comma-separated hostnames / single IPs. Testing deployments may also list
    # private networks in CIDR form (192.168.0.0/16; EGRESS-NET) — production
    # refuses those at startup: use service hostnames there.
    ingestion_internal_source_allowlist: str = ""
    # Connector drivers are pinned to the addresses the egress check validated
    # (no DNS-rebinding window). A driver that resolves hosts outside Python and
    # cannot be pinned (confluent-kafka / librdkafka) is refused while this is on.
    ingestion_egress_strict_pinning: bool = True
    # Backstop: ingestion jobs still running/pending after this long, with no
    # heartbeat for as long, are reaped as failed (legacy rows without a lease,
    # or SYNC-ORPHAN recovery switched off). Must exceed the queued lock TTL (3600s).
    ingestion_stale_job_seconds: int = 7200
    # Upstream-deletion reconciliation (KB-44): a Source whose connector can list
    # what exists upstream (S3/MinIO, GCS, Azure Blob) has the documents deleted
    # there removed from its collection by the ``ingestion.reconcile_source``
    # task. A failure-free sync schedules it at most once per this interval per
    # Source (it lists the whole bucket, so it no longer runs after every sync);
    # ``POST /sources/{id}/reconcile`` runs it on demand.
    ingestion_reconcile_interval_seconds: int = Field(default=86400, ge=300)

    # --- owner decisions (defaults = shipped behaviour) ---
    # Shortest gap a plan may schedule between fires (cron, interval, api_poll
    # floors). The beat ticks every 60 s, so 60 s is the effective minimum.
    schedule_min_interval_free_s: int = Field(default=900, ge=60)
    schedule_min_interval_starter_s: int = Field(default=300, ge=60)
    schedule_min_interval_professional_s: int = Field(default=60, ge=60)
    schedule_min_interval_enterprise_s: int = Field(default=60, ge=60)
    # Collections one federated knowledge search may fan out to.
    knowledge_federated_max_collections: int = Field(default=20, ge=1, le=200)
    # Making an agent fully-autonomous requires an attached eval suite whose
    # latest completed run passes the rollout gate. False skips both checks.
    fully_autonomous_eval_gate_enabled: bool = True
    # The gate's run must reach this pass rate over at least this many golden
    # tasks, against the agent's current config and the suite's current dataset
    # version (MEM-52).
    rollout_min_pass_rate: float = Field(default=0.8, ge=0.0, le=1.0)
    rollout_min_suite_size: int = Field(default=5, ge=1, le=100_000)
    # Durable eval-suite runs (MEM-53): non-blocking worker steps keep at most
    # eval_suite_run_concurrency golden goals in flight per run, claiming tasks
    # under a lease and polling goals every eval_suite_goal_poll_seconds; a
    # task's goal may run
    # max(60, max_iterations * seconds_per_iteration) seconds, capped below.
    eval_suite_run_concurrency: int = Field(default=4, ge=1, le=64)
    eval_suite_lease_seconds: float = Field(default=60.0, ge=5.0, le=3600.0)
    eval_suite_task_seconds_per_iteration: float = Field(default=20.0, ge=1.0, le=600.0)
    eval_suite_task_timeout_max_seconds: float = Field(default=1800.0, ge=10.0, le=86_400.0)
    eval_suite_goal_poll_seconds: float = Field(default=5.0, ge=0.01, le=300.0)
    eval_suite_max_task_attempts: int = Field(default=3, ge=1, le=20)
    # A running run with no worker progress for this long is re-dispatched by the
    # beat sweeper; reads report it "abandoned" after eval_suite_stalled_after_seconds.
    eval_suite_resume_after_seconds: float = Field(default=180.0, ge=10.0, le=86_400.0)
    eval_suite_stalled_after_seconds: float = Field(default=1800.0, ge=30.0, le=604_800.0)
    # AI-Ops dataset runs (P7-1): the same non-blocking design. A run is advanced
    # by short worker steps that submit up to ai_ops_run_concurrency case goals
    # and POLL them every ai_ops_poll_seconds under a lease on the run row; a
    # step never waits inline on a goal (the goals need worker slots too). A
    # case's goal may run ai_ops_case_timeout_seconds from submission; a run
    # whose steps stopped for ai_ops_resume_after_seconds is re-dispatched.
    ai_ops_run_concurrency: int = Field(default=4, ge=1, le=64)
    ai_ops_poll_seconds: float = Field(default=5.0, ge=0.01, le=300.0)
    ai_ops_lease_seconds: float = Field(default=300.0, ge=5.0, le=3600.0)
    ai_ops_case_timeout_seconds: float = Field(default=900.0, ge=1.0, le=86_400.0)
    ai_ops_resume_after_seconds: float = Field(default=600.0, ge=10.0, le=86_400.0)
    # A supervisor's sub-goals run under the parent's concurrent-goal slot
    # (a parent at the tenant limit can never starve its own children). False
    # makes each sub-goal take, and release, a slot of its own.
    subgoals_share_parent_slot: bool = True

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

    llm_require_platform_key: bool = Field(
        default=True,
        description=(
            "Production refuses to start without a platform LLM key. Set "
            "LLM_REQUIRE_PLATFORM_KEY=false for a BYOK-only deployment: tenants "
            "with their own key run, tenants without one get 'no LLM provider "
            "configured' (read via app.providers.llm_resolution.platform_key_required)."
        ),
    )

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

    @field_validator("rag_default_rerank_strategy")
    @classmethod
    def _known_rerank_strategy(cls, value: str) -> str:
        """Refuse a rerank strategy with no implementation (a04-F073-01).

        ``llm`` used to be accepted and silently ran the cross-encoder; an unknown
        name silently ran ``auto``.
        """
        from app.context.rerank_policy import RerankStrategy

        name = (value or "").strip().lower()
        known = sorted(s.value for s in RerankStrategy)
        if name == "llm":
            raise ValueError(
                "RAG_DEFAULT_RERANK_STRATEGY=llm is not implemented (there is no LLM "
                "reranker); use 'hosted' for Model Registry rerank models or "
                f"'cross_encoder'; supported: {', '.join(known)}"
            )
        if name not in known:
            raise ValueError(
                f"RAG_DEFAULT_RERANK_STRATEGY={value!r} is not a rerank strategy; "
                f"supported: {', '.join(known)}"
            )
        return name

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

    @model_validator(mode="after")
    def _refuse_ip_ranges_in_production(self) -> Settings:
        # EGRESS-NET: opening whole private ranges is a testing aid. Production
        # keeps SSRF protection and names its internal services instead. With
        # ALLOW_PRIVATE_NETWORK_ACCESS on (owner decision: every environment) every
        # private range is open anyway and the CIDR entries are moot, so they no
        # longer stop startup; off restores the refusal.
        if (
            self.environment == "production"
            and not self.allow_private_network_access
            and "/" in self.ingestion_internal_source_allowlist
        ):
            raise ValueError(
                "INGESTION_INTERNAL_SOURCE_ALLOWLIST: IP ranges (CIDR) are refused in "
                "production; allowlist service hostnames instead"
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
        # The local compose stack's least-privilege app role and the owner DSNs
        # carry dev-default passwords too.
        for name, dsn in (
            ("DATABASE_URL", settings.database_url),
            ("MAINTENANCE_DATABASE_URL", settings.maintenance_database_url),
            ("MIGRATION_DATABASE_URL", settings.migration_database_url),
        ):
            if "agentverse_app:agentverse_app@" in dsn or (
                name != "DATABASE_URL" and "agentverse:agentverse@" in dsn
            ):
                logging.getLogger(__name__).error(
                    "SECURITY: %s contains a default development password. "
                    "This must be changed before production deployment!",
                    name,
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
