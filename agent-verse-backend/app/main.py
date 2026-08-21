"""FastAPI application factory.

Wires logging, tracing, CORS, security headers, structured error handling, the system
router (health/metrics), and a per-app health-check registry. The factory takes optional
``settings`` and ``health_checks`` so tests can drive it deterministically.

Service wiring (all stored on ``app.state``):
  - tenant_service        : TenantService (API key auth, tenant CRUD)
  - goal_service          : GoalService (goal lifecycle + SSE events)
  - mcp_registry          : MCPRegistry (per-tenant connector registry)
  - mcp_client            : MCPClient (HTTP client for tools/list + tool execution)
  - oauth_manager         : OAuthFlowManager (PKCE OAuth flows)
  - agent_store           : AgentStore (per-tenant agent config store)
  - meta_agent            : MetaAgentPlanner (NL → agent config)
  - hitl_gateway          : HITLGateway (HITL approval queue)
  - audit_log             : AuditLog (append-only event trail)
  - cost_controller       : CostController (per-goal/per-tenant budgets)
  - policy_engine         : PolicyEngine (tool policy evaluation)
  - schedule_store        : ScheduleStore (trigger schedule CRUD)
  - nl_scheduler          : NLScheduler (NL → TriggerSpec)
  - knowledge_store       : KnowledgeStore (hybrid vector + trigram search)
  - semantic_cache        : SemanticCache (LLM call deduplication by embedding)
  - long_term_memory      : LongTermMemoryStore (cross-session learnings)
  - eval_runner           : EvalRunner (5-dimension goal scoring)
  - compliance_controller : ComplianceController (GDPR/SOC2/PCI-DSS)
  - simulation_runner     : SimulationRunner (mock-tool sandbox)
  - red_team_runner       : RedTeamRunner (adversarial tests)
  - marketplace           : Marketplace (template gallery + deploy)
  - self_optimizer        : SelfOptimizer (failed-eval improvement suggestions)
"""

from __future__ import annotations

import asyncio
import builtins
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.agents import AgentStore
from app.api.templates import template_store as _template_store
from app.api.workflows import _WorkflowStore as WorkflowStore
from app.auth.agent_identity import AgentIdentityService
from app.auth.scope_enforcement import ScopeEnforcementMiddleware
from app.collab.store import CollaborationStore
from app.core.config import Settings, get_settings
from app.core.errors import InternalError, PlatformError
from app.core.pools import ConnectionPools
from app.enterprise.compliance import ComplianceController
from app.enterprise.compliance_v2 import ComplianceChecker
from app.enterprise.marketplace import Marketplace
from app.enterprise.marketplace_v2 import MarketplaceV2
from app.enterprise.red_team import RedTeamRunner
from app.enterprise.simulation import SimulationRunner
from app.governance.audit import AuditLog
from app.governance.cost import CostController
from app.governance.hitl import HITLGateway
from app.governance.policies import PolicyEngine
from app.intelligence.cost_tracker import CostTracker
from app.intelligence.eval_runner import EvalRunner
from app.intelligence.eval_suite import EvalSuiteRunner
from app.intelligence.guardrail_engine import GuardrailEngine as GuardrailEngineV2
from app.intelligence.meta_agent import MetaAgentPlanner
from app.intelligence.self_optimization import SelfOptimizer
from app.intelligence.self_optimizer_v2 import SelfOptimizerV2
from app.main_services import get_service_health  # noqa: F401
from app.mcp.client import MCPClient
from app.mcp.oauth import OAuthFlowManager
from app.mcp.registry import MCPRegistry
from app.memory.execution import ExecutionMemory
from app.memory.long_term import LongTermMemoryStore
from app.observability.health import HealthCheck, HealthRegistry
from app.observability.logging import configure_logging, get_logger
from app.observability.tracing import configure_tracing
from app.providers.fake import FakeProvider
from app.providers.vault import (
    RedisConnectorSecretStore,
    get_vault,
    resolve_connector_secret_ref_for_tenant,
)
from app.rag.agentic.patterns.web_augmented import (
    build_safe_web_search_capability,
    parse_allowed_domains,
)
from app.rag.catalogue import RAGAdapterConfiguration
from app.rag.contracts import RAGStrategy
from app.rag.gateway import (
    KnowledgeStoreCollectionAuthorizer,
    ResolvedLLM,
    RetrievalDependencies,
    RetrievalGateway,
    SQLCollectionAuthorizer,
    TenantScopedGraphCapabilityAdapter,
    core_strategy_capabilities,
)
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.rpa.artifacts import get_artifact_store
from app.rpa.session_manager import BrowserSessionManager
from app.services.event_store import EventStore
from app.services.goal_queue import CeleryGoalTaskQueue
from app.services.goal_service import GoalService
from app.services.notification_service import NotificationService
from app.services.tenant_service import TenantService
from app.services.usage_service import UsageService
from app.tenancy.context import TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware
from app.triggers.nl_scheduler import NLScheduler
from app.triggers.store import ScheduleStore

logger = get_logger(__name__)


def _resolve_provider_for_app(settings: Settings) -> Any:
    """Resolve a real LLM provider from environment, or FakeProvider as last resort.

    Uses the declarative ProviderRegistry (Phase 5) which supports Anthropic,
    OpenAI-compatible, Gemini, Groq, and Ollama — auto-detected from env vars
    or driven by the LLM_PROVIDERS JSON array override.
    """
    import os

    from app.providers.registry import resolve_provider

    _app_provider = resolve_provider()

    # Production safety guard: refuse to start with FakeProvider in production.
    if isinstance(_app_provider, FakeProvider):
        env = os.getenv("ENVIRONMENT", "development").lower()
        if env == "production":
            raise RuntimeError(
                "FATAL: No LLM provider configured for production. "
                "Set ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, "
                "GROQ_API_KEY, or OLLAMA_BASE_URL environment variable."
            )
        logger.warning(
            "fake_provider_active_dev_only",
            message=(
                "FakeProvider is active. "
                "Set ANTHROPIC_API_KEY or OPENAI_API_KEY for real goal execution."
            ),
        )
        return FakeProvider(
            responses=[
                '{"steps": ["Complete the requested task"]}',
                "Task executed successfully",
                '{"success": true, "reason": "Goal achieved"}',
            ]
        )

    return _app_provider


def _build_verifier_provider(settings: Any = None) -> Any:
    """Build a separate LLM provider for the verifier role.

    Priority:
    1. VERIFIER_API_KEY env var — explicit separate key.
    2. Cross-model: if OPENAI_API_KEY is primary, try ANTHROPIC_API_KEY for verifier.
    3. Returns None — caller should fall back to primary provider.

    Cross-model verification breaks self-confirmation bias: if the executor
    used OpenAI, having Anthropic verify reduces hallucinated success rates.
    """
    import os

    from app.core.config import get_provider_env

    verifier_key = os.getenv("VERIFIER_API_KEY", "")
    anthropic_key = get_provider_env("ANTHROPIC_API_KEY")
    openai_key = get_provider_env("OPENAI_API_KEY")

    # 1. Explicit dedicated verifier key
    if verifier_key:
        if verifier_key.startswith("sk-ant-"):
            try:
                from app.providers.anthropic_provider import AnthropicProvider

                logger.info("verifier_provider_anthropic_dedicated_key")
                return AnthropicProvider(api_key=verifier_key)
            except Exception as exc:
                logger.warning("verifier_anthropic_init_failed", error=str(exc))
        else:
            try:
                from app.providers.openai_compatible import OpenAICompatibleProvider

                logger.info("verifier_provider_openai_dedicated_key")
                return OpenAICompatibleProvider(api_key=verifier_key)
            except Exception as exc:
                logger.warning("verifier_openai_init_failed", error=str(exc))

    # 2. Cross-model: primary is OpenAI → try Anthropic for verifier
    if openai_key and anthropic_key:
        try:
            from app.providers.anthropic_provider import AnthropicProvider

            logger.info("verifier_provider_anthropic_cross_model")
            return AnthropicProvider(api_key=anthropic_key)
        except Exception as exc:
            logger.warning("verifier_crossmodel_anthropic_failed", error=str(exc))

    # 3. No separate provider available
    logger.info(
        "verifier_provider_same_as_primary",
        message=(
            "No separate verifier key — verifier reuses executor provider. "
            "Set VERIFIER_API_KEY or both OPENAI_API_KEY + ANTHROPIC_API_KEY "
            "for cross-model verification."
        ),
    )
    return None


# ── Minimal in-memory Redis fallback (used when pools are not started) ─────────


class _FakeRedis:
    """Thread/async-safe dict-backed Redis stub — for tests and no-pool mode.

    Supports string ops, set ops, and sorted-set ops required by
    :class:`~app.tenancy.rate_limiter.SlidingWindowRateLimiter`.
    """

    def __init__(self) -> None:
        import asyncio as _asyncio

        self._d: dict[str, Any] = {}
        self._s: dict[str, set[str]] = {}
        # sorted sets: key → {member: score}
        self._z: dict[str, dict[str, float]] = {}
        # TTL tracking: key → expiry epoch (monotonic seconds)
        self._ttl: dict[str, float] = {}
        # Lazy asyncio.Lock — created on first use to avoid event-loop issues at
        # construction time (the _FakeRedis instance is created before the loop starts).
        self._lock: _asyncio.Lock | None = None

    def _get_lock(self) -> Any:
        """Return the asyncio.Lock, creating it lazily on first use."""
        import asyncio as _asyncio

        if self._lock is None:
            self._lock = _asyncio.Lock()
        return self._lock

    def _is_expired(self, key: str) -> bool:
        import time

        exp = self._ttl.get(key)
        return exp is not None and time.monotonic() > exp

    async def get(self, key: str) -> str | None:
        if self._is_expired(key):
            self._d.pop(key, None)
            self._ttl.pop(key, None)
            return None
        return self._d.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, **kwargs: Any) -> None:
        self._d[key] = value
        if ex is not None:
            import time

            self._ttl[key] = time.monotonic() + ex

    async def delete(self, key: str) -> int:
        existed = key in self._d
        self._d.pop(key, None)
        return int(existed)

    async def sadd(self, key: str, member: str) -> None:
        self._s.setdefault(key, set()).add(member)

    async def srem(self, key: str, member: str) -> None:
        self._s.get(key, set()).discard(member)

    async def smembers(self, key: str) -> builtins.set[str]:
        return self._s.get(key, builtins.set())

    # ── sorted-set ops (required by SlidingWindowRateLimiter) ─────────────────

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        async with self._get_lock():
            zset = self._z.setdefault(key, {})
            added = sum(1 for m in mapping if m not in zset)
            zset.update(mapping)
            return added

    async def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        async with self._get_lock():
            if self._is_expired(key):
                self._z.pop(key, None)
                self._ttl.pop(key, None)
                return 0
            zset = self._z.get(key)
            if zset is None:
                return 0
            before = len(zset)
            self._z[key] = {
                member: score
                for member, score in zset.items()
                if not (min_score <= score <= max_score)
            }
            return before - len(self._z[key])

    async def zcard(self, key: str) -> int:
        async with self._get_lock():
            if self._is_expired(key):
                self._z.pop(key, None)
                self._ttl.pop(key, None)
                return 0
            return len(self._z.get(key, {}))

    async def expire(self, key: str, seconds: int) -> bool:
        import time

        self._ttl[key] = time.monotonic() + seconds
        return key in self._d or key in self._z

    async def expireat(self, key: str, timestamp: int) -> bool:
        """Set expiry as an absolute Unix timestamp."""
        import time

        self._ttl[key] = float(timestamp) - time.time() + time.monotonic()
        return key in self._d or key in self._z

    async def incrbyfloat(self, key: str, amount: float) -> float:
        """Increment a float counter and return the new value."""
        async with self._get_lock():
            current = float(self._d.get(key, 0))
            new_val = current + amount
            self._d[key] = str(new_val)
            return new_val

    def register_script(self, script: str) -> _FakeLuaScript:
        """Return a fake Lua script executor that simulates the atomic check-and-increment."""
        return _FakeLuaScript(self, script)


class _FakeLuaScript:
    """Simulates the _ATOMIC_INCREMENT_SCRIPT Lua behaviour for tests."""

    def __init__(self, redis: _FakeRedis, script: str) -> None:
        self._redis = redis
        self._script = script

    async def __call__(self, keys: list[str], args: list[str]) -> str:
        """Execute the Lua script atomically using the FakeRedis lock.

        Handles two script formats:
        * 1-key / 3-arg  — legacy ``try_record_and_check`` daily-only script.
        * 2-key / 5-arg  — ``check_and_record_async`` goal+daily script.
        """
        import time as _time

        async with self._redis._get_lock():
            if len(keys) == 2:
                # args: cost, goal_limit, daily_limit, goal_expiry, daily_expiry
                goal_key, daily_key = keys[0], keys[1]
                cost = float(args[0])
                goal_limit = float(args[1])
                daily_limit = float(args[2])
                goal_expiry = int(args[3])
                daily_expiry = int(args[4])

                goal_current = float(self._redis._d.get(goal_key, 0))
                daily_current = float(self._redis._d.get(daily_key, 0))

                if goal_limit > 0 and goal_current + cost > goal_limit:
                    raise Exception("GOAL_BUDGET_EXCEEDED")
                if daily_limit > 0 and daily_current + cost > daily_limit:
                    raise Exception("DAILY_BUDGET_EXCEEDED")

                new_goal = goal_current + cost
                new_daily = daily_current + cost
                self._redis._d[goal_key] = str(new_goal)
                self._redis._d[daily_key] = str(new_daily)
                now = _time.time()
                mono = _time.monotonic()
                self._redis._ttl[goal_key] = float(goal_expiry) - now + mono
                self._redis._ttl[daily_key] = float(daily_expiry) - now + mono
                return f"{new_goal}:{new_daily}"
            else:
                # Legacy 1-key / 3-arg format used by try_record_and_check
                key = keys[0]
                increment = float(args[0])
                limit = float(args[1])
                expiry_ts = int(args[2])
                current = float(self._redis._d.get(key, 0))
                if current + increment > limit:
                    raise Exception("BUDGET_EXCEEDED")
                new_val = current + increment
                self._redis._d[key] = str(new_val)
                self._redis._ttl[key] = float(expiry_ts) - _time.time() + _time.monotonic()
                return str(new_val)


# ── error handlers ─────────────────────────────────────────────────────────────


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PlatformError)
    async def _platform_error_handler(_: Request, exc: PlatformError) -> JSONResponse:
        if exc.severity.value in {"high", "critical"}:
            logger.error("platform_error", code=exc.code, error_id=exc.error_id)
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        # Never leak internal detail to the client; log the real cause server-side.
        internal = InternalError("An internal error occurred", cause=exc)
        logger.error("unhandled_error", error_id=internal.error_id, exc_info=exc)
        return JSONResponse(internal.to_dict(), status_code=internal.http_status)


# ── factory ────────────────────────────────────────────────────────────────────


def create_app(
    settings: Settings | None = None,
    health_checks: Sequence[HealthCheck] | None = None,
    pools: ConnectionPools | None = None,
    manage_pools: bool = False,
    # Optional service overrides (accept Any so tests can pass duck-typed fakes)
    tenant_service: Any = None,
    goal_service: Any = None,
    mcp_registry: MCPRegistry | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.is_production)

    # Allow env var to enable manage_pools when uvicorn calls create_app() with no args
    import os as _os_mp

    if not manage_pools and _os_mp.getenv("MANAGE_POOLS", "").lower() in ("1", "true", "yes"):
        manage_pools = True

    registry = HealthRegistry(list(health_checks or []))

    # ── Build shared services ─────────────────────────────────────────────────
    _tenant_svc = tenant_service or TenantService()
    _audit_log = AuditLog()
    _hitl = HITLGateway()
    _cost = CostController()
    _policy_engine = PolicyEngine()
    _agent_store = AgentStore()
    # C6: Use the declarative provider registry directly; fall back to wrapper on error
    try:
        from app.providers.registry import resolve_provider as _resolve_provider_registry

        _app_provider = _resolve_provider_registry()
        # Production safety guard: refuse FakeProvider in production
        if isinstance(_app_provider, FakeProvider):
            import os as _os

            if _os.getenv("ENVIRONMENT", "development").lower() == "production":
                raise RuntimeError(
                    "FATAL: No LLM provider configured for production. "
                    "Set ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, "
                    "GROQ_API_KEY, or OLLAMA_BASE_URL environment variable."
                )
            logger.warning(
                "fake_provider_active_dev_only",
                message="FakeProvider active — set ANTHROPIC_API_KEY or OPENAI_API_KEY.",
            )
            _app_provider = FakeProvider(
                responses=[
                    '{"steps": ["Complete the requested task"]}',
                    "Task executed successfully",
                    '{"success": true, "reason": "Goal achieved"}',
                ]
            )
        logger.info("provider_resolved_via_registry")
    except Exception as _reg_exc:
        logger.warning("provider_registry_failed_fallback", error=str(_reg_exc)[:60])
        _app_provider = _resolve_provider_for_app(settings)
    _meta_agent = MetaAgentPlanner(provider=_app_provider)
    _schedule_store = ScheduleStore()
    _nl_sched = NLScheduler(provider=_app_provider)
    _knowledge_store = KnowledgeStore()
    _semantic_cache = SemanticCache()
    # Ingestion framework — LAW-01: single pipeline path
    try:
        from app.ingestion.connector_registry import load_all_connectors
        from app.ingestion.job_tracker import IngestionJobTracker
        from app.ingestion.pipeline import IngestionPipeline

        load_all_connectors()
        _ingestion_pipeline = IngestionPipeline(
            knowledge_store=_knowledge_store,
            embedder=_app_provider,
        )
        _ingestion_job_tracker = IngestionJobTracker()
    except Exception as _ing_exc:
        import logging as _lg

        _lg.getLogger(__name__).warning("ingestion_framework_init_error: %s", _ing_exc)
        _ingestion_pipeline = None
        _ingestion_job_tracker = None
    # In-memory ToolResultCache (upgraded with Redis in lifespan)
    try:
        from app.mcp.tool_cache import ToolResultCache

        _tool_cache_inmem = ToolResultCache()
    except Exception:
        _tool_cache_inmem = None
    _fake_redis = _FakeRedis()
    _mcp_registry = mcp_registry or MCPRegistry(redis=_fake_redis)
    _oauth_manager = OAuthFlowManager()
    _long_term_memory = LongTermMemoryStore()
    _eval_runner = EvalRunner()
    _eval_suite_runner = EvalSuiteRunner()
    _compliance_controller = ComplianceController()
    _simulation_runner = SimulationRunner()
    _red_team_runner = RedTeamRunner()
    _marketplace = Marketplace(agent_store=_agent_store)
    _marketplace_v2 = MarketplaceV2(db_factory=None)  # upgraded in lifespan
    _self_optimizer = SelfOptimizer()
    # v2 self-optimizer: fixes all 4 critical bugs + Bayesian A/B testing
    # db_factory and redis are None here; upgraded in lifespan
    _self_optimizer_v2 = SelfOptimizerV2(
        redis=_fake_redis,
        db_factory=None,
        llm_provider_factory=lambda: _app_provider,
    )
    # v2 compliance checker: no hardcoded booleans; db_factory upgraded in lifespan
    _compliance_checker = ComplianceChecker(db_factory=None)
    _notification_service = NotificationService()
    # H-3: ExecutionMemory — wired with DB in lifespan
    _exec_memory = ExecutionMemory()
    # Cost tracker — wired with real Redis + DB in lifespan
    _cost_tracker = CostTracker(redis=_fake_redis)
    # AgentIdentityService — wired with DB + Redis in lifespan
    _agent_identity_svc = AgentIdentityService(db=None, vault=get_vault(), redis=_fake_redis)
    # GuardrailEngine v2 — wired with Redis in lifespan
    _guardrail_engine_v2 = GuardrailEngineV2()

    # Wire embedder: use VoyageProvider if VOYAGE_API_KEY set,
    # OpenAICompatibleProvider if OPENAI_API_KEY set,
    # LocalEmbedProvider if SENTENCE_TRANSFORMERS_MODEL set, else None.
    import os

    _embedder: Any = None
    from app.core.config import get_provider_env

    _openai_key = get_provider_env("OPENAI_API_KEY")
    _voyage_key = get_provider_env("VOYAGE_API_KEY")
    _anthropic_key = get_provider_env("ANTHROPIC_API_KEY")
    if _voyage_key:
        try:
            from app.providers.voyage_provider import VoyageProvider

            _embedder = VoyageProvider(api_key=_voyage_key)
        except Exception:
            pass
    elif _openai_key:
        try:
            from app.providers.openai_compatible import OpenAICompatibleProvider

            _embedder = OpenAICompatibleProvider(
                api_key=_openai_key, default_model="text-embedding-3-small"
            )
        except Exception:
            pass
    elif get_provider_env("GOOGLE_API_KEY"):
        try:
            from app.providers.gemini_provider import GeminiProvider

            _embedder = GeminiProvider(api_key=get_provider_env("GOOGLE_API_KEY"))
        except Exception:
            pass
    elif os.getenv("SENTENCE_TRANSFORMERS_MODEL", ""):
        try:
            from app.providers.voyage_provider import LocalEmbedProvider

            _embedder = LocalEmbedProvider(
                model_name=os.getenv("SENTENCE_TRANSFORMERS_MODEL", "all-MiniLM-L6-v2")
            )
            logger.info(
                "local_embed_provider_wired",
                model=os.getenv("SENTENCE_TRANSFORMERS_MODEL"),
            )
        except Exception as _exc:
            logger.warning("local_embed_provider_failed", error=str(_exc))
    # app.state.embedder is set after app = FastAPI(...)

    # Wire ModelRouter: selects optimal model per task type based on available provider
    from app.agent.model_router import ModelRouter

    try:
        _mr_provider = "openai" if _openai_key else ("anthropic" if _anthropic_key else "anthropic")
        _model_router: Any = ModelRouter(provider_name=_mr_provider)
    except Exception as _mr_exc:
        _model_router = None
        logger.warning("model_router_init_failed", error=str(_mr_exc))

    async def _resolve_retrieval_llm(
        tenant_context: TenantContext,
        strategy: RAGStrategy,
    ) -> ResolvedLLM | None:
        del strategy
        tenant_config: dict[str, Any] | None = None
        config_store = getattr(app.state, "llm_config_store", None)
        if config_store is not None:
            tenant_config = await config_store.get_config(tenant_context.tenant_id)
        if tenant_config is None:
            tenant_config = getattr(app.state, "_llm_configs", {}).get(tenant_context.tenant_id)

        if tenant_config is not None:
            encrypted_key = str(tenant_config.get("encrypted_key") or "")
            provider_name = str(tenant_config.get("provider") or "")
            configured_model = str(
                tenant_config.get("model") or tenant_config.get("default_model") or ""
            ).strip()
            if not encrypted_key or not provider_name:
                return None
            try:
                from app.providers.registry import instantiate_configured_provider

                api_key = get_vault().decrypt(encrypted_key)
                provider = instantiate_configured_provider(
                    provider_name,
                    api_key=api_key,
                    model=configured_model,
                    base_url=str(tenant_config.get("base_url") or ""),
                )
            except Exception as exc:
                logger.warning(
                    "tenant_retrieval_provider_resolution_failed",
                    tenant_id=tenant_context.tenant_id,
                    error=type(exc).__name__,
                )
                return None
            if provider is None:
                return None
            model = configured_model
            if not model:
                provider_default = getattr(provider, "_default_model", "")
                model = provider_default.strip() if isinstance(provider_default, str) else ""
            return (
                ResolvedLLM(
                    provider=provider,
                    model=model,
                    provider_type=provider_name.strip().lower(),
                )
                if model
                else None
            )

        provider_default = getattr(_app_provider, "_default_model", "")
        model = provider_default.strip() if isinstance(provider_default, str) else ""
        if not model and isinstance(_app_provider, FakeProvider):
            model = "fake-provider"
        if not model:
            return None
        return ResolvedLLM(
            provider=_app_provider,
            model=model,
            provider_type=str(getattr(_app_provider, "_agentverse_provider_type", "")),
        )

    _web_search_capability = build_safe_web_search_capability(
        searxng_url=settings.searxng_url,
        policy_services=(_policy_engine, _cost, _hitl),
        allowed_domains=parse_allowed_domains(settings.web_search_allowed_domains),
    )
    from app.rag.raft import InMemoryRAFTRepository, RAFTService

    _raft_service = RAFTService(
        repository=InMemoryRAFTRepository(),
        providers={},
    )
    _rag_adapter_configuration = RAGAdapterConfiguration(
        colbert_checkpoint=settings.colbert_checkpoint
    )
    _retrieval_gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=None,
            embedder=_embedder,
            llm_resolver=_resolve_retrieval_llm,
            graph_capability=None,
            search_capability=_web_search_capability,
            policy_services=(_policy_engine, _cost, _hitl),
            cost_controller=_cost,
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(_knowledge_store),
            strategy_capabilities=core_strategy_capabilities(_rag_adapter_configuration),
            raft_service=_raft_service,
            colbert_checkpoint=settings.colbert_checkpoint,
        )
    )
    _retrieval_gateways_to_close: dict[int, object] = {id(_retrieval_gateway): _retrieval_gateway}

    from app.rpa.executor import RPAExecutor
    from app.rpa.session import RPASessionStore

    _rpa_session_manager = BrowserSessionManager()
    _rpa_artifact_store = get_artifact_store()

    # Determine whether the embedder supports vision for screenshot analysis
    _supports_vision = (
        _embedder is not None
        and hasattr(_embedder, "supports_vision")
        and _embedder.supports_vision()
    )
    _rpa_executor = RPAExecutor(
        session_manager=_rpa_session_manager,
        artifact_store=_rpa_artifact_store,
        vision_provider=_embedder if _supports_vision else None,
    )
    _rpa_session_store = RPASessionStore()

    # Perception
    from app.perception.browser_agent import BrowserAgent
    from app.perception.page_analyzer import PageAnalyzer

    _browser_agent = BrowserAgent(vision_provider=_embedder if _supports_vision else None)
    _page_analyzer = PageAnalyzer(browser_agent=_browser_agent)

    _task_queue = CeleryGoalTaskQueue() if manage_pools and settings.redis_url else None
    _goal_svc = goal_service or GoalService(
        audit_log=_audit_log, hitl=_hitl, task_queue=_task_queue
    )

    # Wire service references into the compliance controller for data export.
    # These are the non-DB services; the lifespan re-wires with DB-backed ones.
    _compliance_controller.configure_services(
        goal_service=_goal_svc,
        audit_log=_audit_log,
        agent_store=_agent_store,
        schedule_store=_schedule_store,
        knowledge_store=_knowledge_store,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async def close_retrieval_gateways() -> None:
            active_gateway = getattr(app.state, "retrieval_gateway", None)
            if active_gateway is not None:
                _retrieval_gateways_to_close[id(active_gateway)] = active_gateway
            gateways = tuple(_retrieval_gateways_to_close.values())
            _retrieval_gateways_to_close.clear()

            async def close_gateway(gateway: object) -> None:
                close = getattr(gateway, "aclose", None)
                if close is not None:
                    await close()

            results = await asyncio.gather(
                *(close_gateway(gateway) for gateway in gateways),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    logger.warning(
                        "retrieval_gateway_close_failed",
                        failure_type=type(result).__name__,
                    )

        async def close_process_rerankers() -> None:
            from app.rag.cross_encoder import close_default_cross_encoder

            await close_default_cross_encoder()

        if manage_pools:
            active = pools or ConnectionPools(settings=settings)
            await active.startup()
            for check in active.health_checks():
                registry.register(check)
            app.state.pools = active
            real_redis = active.redis
            redis_for_runtime: Any = None
            if real_redis is not None:
                redis_for_runtime = real_redis
                app.state.mcp_registry = MCPRegistry(redis=real_redis)
                app.state.connector_secret_store = RedisConnectorSecretStore(
                    redis=real_redis,
                    vault=get_vault(),
                )
                app.state.connector_secret_store_is_production_safe = True
                app.state.mcp_client = _make_mcp_client(app.state.mcp_registry)
                # Re-wire MCP client into tool inverse registry with the real Redis-backed client
                from app.reliability.tool_inverses import set_mcp_client as _set_inv_mcp

                _set_inv_mcp(app.state.mcp_client)
                # Wire Redis-backed CostController for cross-replica budget accuracy
                from app.governance.cost import RedisCostController

                _redis_cost_ctrl = RedisCostController(redis=real_redis)
                app.state.redis_cost_controller = _redis_cost_ctrl
                # Upgrade CostTracker to use real Redis
                _cost_tracker._redis = real_redis
                # Also patch _redis on the in-memory controller so it can fall back
                if hasattr(app.state, "cost_controller"):
                    app.state.cost_controller._redis = real_redis
                # Upgrade rate-limiter from in-memory stub to real Redis so all
                # replicas share one rate-limit counter per tenant.
                app.state._rate_limiter_redis = real_redis
                # Wire LLM config store so Celery workers can read tenant configs.
                from app.services.llm_config_store import LLMConfigStore, set_llm_config_store

                _llm_store = LLMConfigStore(redis_client=real_redis)
                set_llm_config_store(_llm_store)
                app.state.llm_config_store = _llm_store
                # Wire RedisSaver checkpointer for persistent LangGraph state (Fix 7 + Fix 2)
                # langgraph-checkpoint-redis >= 0.0.6 returns an async context manager from
                # from_conn_string(); we must enter it via __aenter__ to get the real saver.
                # Sync RedisSaver.from_conn_string() similarly returns a sync context manager.
                try:
                    from langgraph.checkpoint.redis.aio import AsyncRedisSaver

                    _raw_cm = AsyncRedisSaver.from_conn_string(str(settings.redis_url))
                    if hasattr(_raw_cm, "__aenter__"):
                        # Newer library: async context manager — enter it to get the saver
                        _actual_saver = await _raw_cm.__aenter__()
                        # Keep a reference so the context manager stays alive for the
                        # full process lifetime; cleaned up in the finally block below.
                        app.state._async_redis_saver_cm = _raw_cm
                    else:
                        _actual_saver = _raw_cm
                    await _actual_saver.setup()  # create Redis data structures
                    app.state.langgraph_checkpointer = _actual_saver
                    logger.info("async_redis_saver_checkpointer_wired")
                except (ImportError, Exception) as _exc:
                    logger.warning("async_redis_saver_failed", error=str(_exc))
                    # Fall back to sync RedisSaver
                    try:
                        from langgraph.checkpoint.redis import RedisSaver

                        _raw_sync_cm = RedisSaver.from_conn_string(str(settings.redis_url))
                        if hasattr(_raw_sync_cm, "__enter__"):
                            _sync_saver = _raw_sync_cm.__enter__()
                            app.state._sync_redis_saver_cm = _raw_sync_cm
                        else:
                            _sync_saver = _raw_sync_cm
                        app.state.langgraph_checkpointer = _sync_saver
                        logger.info("redis_saver_checkpointer_wired")
                    except (ImportError, Exception) as _exc2:
                        logger.warning("redis_saver_unavailable", error=str(_exc2))
                        from langgraph.checkpoint.memory import MemorySaver

                        app.state.langgraph_checkpointer = MemorySaver()
                        logger.warning("using_memory_saver_checkpointer_no_persistence")
            else:
                # Pool didn't provide a Redis client; fall back to a direct
                # connection from REDIS_URL (e.g. when using a minimal pool config).
                _redis_url = settings.redis_url
                if _redis_url:
                    try:
                        from app.net.redis_factory import get_redis_kwargs, make_async_redis

                        _direct_redis: Any = make_async_redis(
                            **get_redis_kwargs(),
                            decode_responses=True,
                        )
                        redis_for_runtime = _direct_redis
                        app.state._rate_limiter_redis = _direct_redis
                        app.state.connector_secret_store = RedisConnectorSecretStore(
                            redis=_direct_redis,
                            vault=get_vault(),
                        )
                        app.state.connector_secret_store_is_production_safe = True
                        from app.services.llm_config_store import (
                            LLMConfigStore,
                            set_llm_config_store,
                        )

                        _llm_store = LLMConfigStore(redis_client=_direct_redis)
                        set_llm_config_store(_llm_store)
                        app.state.llm_config_store = _llm_store
                    except Exception as exc:
                        logger.warning("Could not connect to Redis for rate limiter: %s", exc)

            # Wire DB session factory into services so they persist to PostgreSQL.
            from app.db.session import get_session_factory

            db_factory = get_session_factory()
            app.state.db_session_factory = db_factory
            event_store = EventStore(db_factory)
            from app.coordination.service import CoordinationService
            from app.coordination.store import CoordinationStore

            app.state.coordination_service = CoordinationService(CoordinationStore(db_factory))
            from app.coordination.auction.repository import (
                PostgresAuctionRepository,
                PostgresSealedBidInbox,
            )
            from app.coordination.camel.repository import PostgresCamelRepository
            from app.coordination.generative.repository import (
                PostgresGenerativeRepository,
            )
            from app.coordination.handoffs.membership import (
                DatabaseHandoffMembership,
                DatabaseSessionAuthorizer,
            )
            from app.coordination.handoffs.repository import PostgresHandoffRepository
            from app.coordination.handoffs.service import HandoffService
            from app.coordination.ledger.repository import (
                PostgresProgressLedgerRepository,
            )
            from app.coordination.moa.repository import PostgresMoARepository
            from app.coordination.replay import SequenceReplay
            from app.coordination.store import PostgresReplayRepository
            from app.coordination.swarm.repository import PostgresSwarmRepository
            from app.coordination.transcript.repository import PostgresTranscriptRepository
            from app.coordination.transcript.service import TranscriptService

            app.state.transcript_service = TranscriptService(
                PostgresTranscriptRepository(db_factory)
            )
            app.state.handoff_service = HandoffService(
                PostgresHandoffRepository(db_factory),
                membership=DatabaseHandoffMembership(lambda: db_factory),
            )
            app.state.camel_repository = PostgresCamelRepository(db_factory)
            app.state.generative_repository = PostgresGenerativeRepository(db_factory)
            app.state.swarm_repository = PostgresSwarmRepository(db_factory)
            app.state.auction_repository = PostgresAuctionRepository(db_factory)
            app.state.auction_bid_inbox = PostgresSealedBidInbox(db_factory)
            app.state.coordination_session_authorizer = DatabaseSessionAuthorizer(
                lambda: db_factory
            )
            app.state.coordination_replay = SequenceReplay(PostgresReplayRepository(db_factory))
            app.state.progress_ledger_repository = PostgresProgressLedgerRepository(db_factory)
            app.state.moa_repository = PostgresMoARepository(db_factory)
            from app.routing_runtime.decision_store import PostgresDecisionStore
            from app.routing_runtime.embedding_router import (
                EmbeddingRouter as CanonicalEmbeddingRouter,
            )
            from app.routing_runtime.model_router import ModelRouter as CanonicalModelRouter
            from app.routing_runtime.skill_router import SkillRouter as CanonicalSkillRouter

            app.state.routing_decision_store = PostgresDecisionStore(db_factory)
            app.state.canonical_model_router = CanonicalModelRouter(
                decision_store=app.state.routing_decision_store
            )
            app.state.canonical_skill_router = CanonicalSkillRouter(
                decision_store=app.state.routing_decision_store
            )
            app.state.canonical_embedding_router = CanonicalEmbeddingRouter(
                decision_store=app.state.routing_decision_store
            )
            from app.memory.postgres_repository import PostgresMemoryRepository

            app.state.memory_repository = PostgresMemoryRepository(db_factory)
            from app.memory.reflexion import ReflexionService

            app.state.reflexion_service = ReflexionService(repository=app.state.memory_repository)
            from app.intelligence.improvement_action_executor import (
                ImprovementActionExecutor,
            )
            from app.intelligence.learning_experiments import LearningExperimentService
            from app.memory.prospective import ProspectiveMemoryService

            app.state.prospective_memory_service = ProspectiveMemoryService()
            app.state.learning_experiment_service = LearningExperimentService()
            app.state.improvement_action_executor = ImprovementActionExecutor(handlers={})

            # Wire DB into UsageService so buffer flushes actually reach Postgres.
            _usage_svc = getattr(app.state, "usage_service", None)
            if _usage_svc is not None:
                _usage_svc._db = db_factory
                logger.info("usage_service_db_wired")

            _tenant_svc_with_db = TenantService(db_session_factory=db_factory)
            _goal_svc_with_db = GoalService(
                audit_log=_audit_log,
                hitl=_hitl,
                db_session_factory=db_factory,
                event_store=event_store,
                task_queue=_task_queue,
            )
            _agent_store_with_db = AgentStore(db_session_factory=db_factory)

            # Hydrate in-memory state from DB (idempotent — skips keys already present)
            await _tenant_svc_with_db.sync_from_db()
            await _goal_svc_with_db.sync_from_db()
            await _agent_store_with_db.sync_from_db()

            app.state.tenant_service = _tenant_svc_with_db
            app.state.goal_service = _goal_svc_with_db
            app.state.goal_service._app_state = app
            app.state.event_store = event_store
            app.state.agent_store = _agent_store_with_db

            # Register built-in MCP servers for every active tenant when their
            # required env vars are present. Without this, catalog connectors
            # can exist in Redis with no tool_definitions/handler, causing
            # agents to fail with "Tool not found" even though the connector
            # appears registered in the UI.
            try:
                from app.mcp.servers.registry_wiring import register_builtin_servers
                from app.tenancy.context import PlanTier, TenantContext

                builtin_registered = 0
                for tenant_id, tenant_data in getattr(_tenant_svc_with_db, "_tenants", {}).items():
                    try:
                        tenant_plan = PlanTier(tenant_data.get("plan", "free"))
                    except Exception:
                        tenant_plan = PlanTier.FREE
                    tenant_ctx = TenantContext(
                        tenant_id=tenant_id,
                        plan=tenant_plan,
                        api_key_id="builtin-registration",
                        roles=("admin",),
                    )
                    builtin_registered += await register_builtin_servers(
                        app.state.mcp_registry,
                        tenant_ctx,
                    )
                logger.info("builtin_mcp_servers_registered", count=builtin_registered)
            except Exception as _builtin_exc:
                logger.warning("builtin_mcp_server_registration_failed", error=str(_builtin_exc))

            # H-3: Wire DB into ExecutionMemory for persistence
            _exec_memory._db = db_factory
            app.state.exec_memory = _exec_memory

            # Wire ToolReliabilityStore for cross-restart tool reliability data
            try:
                from app.memory.tool_reliability import ToolReliabilityStore

                _tool_reliability = ToolReliabilityStore(db_session_factory=db_factory)
                app.state.tool_reliability_store = _tool_reliability
                logger.info("tool_reliability_store_wired")
            except Exception as _tr_exc:
                logger.warning("tool_reliability_store_wire_failed", error=str(_tr_exc))

            # Seed execution memory from DB for faster cold-start recall()
            try:
                import asyncio as _em_asyncio

                async def _hydrate_exec_memory() -> None:
                    try:
                        _em_rows = None
                        async with db_factory() as _em_sess:
                            from sqlalchemy import text as _t

                            _em_rows = (
                                await _em_sess.execute(
                                    _t("SELECT DISTINCT tenant_id FROM execution_memory LIMIT 50")
                                )
                            ).fetchall()
                        if _em_rows:
                            for (_em_tid,) in _em_rows:
                                await _exec_memory.load_from_db(tenant_id=_em_tid, db=db_factory)
                        logger.info("execution_memory_hydrated", tenant_count=len(_em_rows or []))
                    except Exception as _em_inner_err:
                        logger.warning(
                            "execution_memory_hydration_failed", error=str(_em_inner_err)
                        )

                _em_asyncio.create_task(_hydrate_exec_memory())
            except Exception as _em_exc:
                logger.warning("execution_memory_hydration_setup_failed", error=str(_em_exc))

            # Wire DB factory into LongTermMemoryStore
            try:
                if hasattr(_long_term_memory, "_db_factory"):
                    _long_term_memory._db_factory = db_factory
                elif hasattr(_long_term_memory, "_db"):
                    _long_term_memory._db = db_factory
                logger.info("long_term_memory_db_wired")
            except Exception as _ltm_exc:
                logger.warning("long_term_memory_db_wire_failed", error=str(_ltm_exc))

            # Wire DB into CostTracker for ledger persistence + historical queries
            _cost_tracker._db = db_factory
            app.state.cost_tracker = _cost_tracker

            # Wire AgentIdentityService with DB session factory
            _agent_identity_svc.set_db(db_factory)
            logger.info("agent_identity_service_db_wired")

            # H-4: Wire agent store directly into goal_service for agent config loading
            _goal_svc_with_db._agent_store = _agent_store_with_db

            # Wire DB session factory into AgentRouter for history scoring
            _agent_router_state = getattr(app.state, "agent_router", None)
            if _agent_router_state is not None:
                _agent_router_state._db = db_factory

            # Wire DB persistence into AuditLog, ScheduleStore, KnowledgeStore
            from app.governance.audit import AuditLog as AuditLogClass
            from app.rag.store import KnowledgeStore as KnowledgeStoreClass
            from app.triggers.store import ScheduleStore as ScheduleStoreClass

            _audit_log_db = AuditLogClass(db_session_factory=db_factory)
            _schedule_store_db = ScheduleStoreClass(
                db_session_factory=db_factory,
                redis=redis_for_runtime,
            )
            _knowledge_store_db = KnowledgeStoreClass(db_session_factory=db_factory)
            _collab_store_db = CollaborationStore(db_session_factory=db_factory)

            await _audit_log_db.sync_from_db()
            await _schedule_store_db.sync_from_db()

            app.state.audit_log = _audit_log_db
            app.state.schedule_store = _schedule_store_db
            app.state.knowledge_store = _knowledge_store_db
            app.state.collab_store = _collab_store_db

            # Wire DB session factory into WorkflowStore for Postgres-backed persistence
            _workflow_store = getattr(app.state, "workflow_store", None)
            if _workflow_store is not None:
                _workflow_store.set_db(db_factory)
                logger.info("workflow_store_db_wired")

            # Wire DB into TemplateStore
            from app.api.templates import template_store as _tmpl_store_ref

            _tmpl_store_ref.set_db(db_factory)
            logger.info("template_store_db_wired")

            # Wire DB into MarketplaceV2 and seed builtin templates
            _marketplace_v2._db = db_factory
            app.state.marketplace_v2 = _marketplace_v2
            try:
                _seeded = await _marketplace_v2.seed_builtins()
                logger.info("marketplace_v2_builtins_seeded", count=_seeded)
            except Exception as _seed_exc:
                logger.warning("marketplace_v2_seed_failed", error=str(_seed_exc))

            # Wire DB into NotificationService for persistent channel storage
            _notif_svc = getattr(app.state, "notification_service", None)
            if _notif_svc is not None:
                _notif_svc.set_db(db_factory)
                await _notif_svc.sync_from_db()
                logger.info("notification_service_db_wired")

            # Wire DB into MFAStore for persistent TOTP secret + recovery-code storage
            try:
                from app.api.mfa import _mfa_db_store as _mfa_store_ref

                _mfa_store_ref.set_db(db_factory)
                logger.info("mfa_db_store_wired")
            except Exception as _mfa_exc:
                logger.warning("mfa_db_store_wire_failed", error=str(_mfa_exc))

            # Wire DB into KnowledgeGraphStore for persistent node/edge storage
            _graph_capability = None
            try:
                from app.knowledge_graph.store import kg_store as _kg_store

                _kg_store.set_db(db_factory)
                _graph_capability = TenantScopedGraphCapabilityAdapter()
                logger.info("knowledge_graph_db_wired")
                # Per-tenant hydration is handled lazily in query_nodes() on first miss.
            except Exception as _kg_exc:
                logger.warning("knowledge_graph_db_wire_failed", error=str(_kg_exc))

            from app.rag.raft_repository import SQLRAFTRepository

            db_retrieval_gateway = RetrievalGateway(
                RetrievalDependencies(
                    session_factory=db_factory,
                    embedder=app.state.embedder,
                    llm_resolver=_resolve_retrieval_llm,
                    graph_capability=_graph_capability,
                    search_capability=_web_search_capability,
                    policy_services=(
                        _policy_engine,
                        getattr(app.state, "redis_cost_controller", _cost),
                        _hitl,
                    ),
                    cost_controller=getattr(app.state, "redis_cost_controller", _cost),
                    collection_authorizer=SQLCollectionAuthorizer(),
                    strategy_capabilities=core_strategy_capabilities(_rag_adapter_configuration),
                    raft_service=RAFTService(
                        repository=SQLRAFTRepository(db_factory),
                        providers={},
                    ),
                    colbert_checkpoint=settings.colbert_checkpoint,
                )
            )
            await close_retrieval_gateways()
            app.state.retrieval_gateway = db_retrieval_gateway
            app.state.raft_service = db_retrieval_gateway.dependencies.raft_service
            _retrieval_gateways_to_close[id(db_retrieval_gateway)] = db_retrieval_gateway

            # Wire DB into reflexion wirer singleton for cross-process persistence
            try:
                from app.agent.reflexion_wirer import get_reflexion_wirer as _get_rw

                _rw = _get_rw(db_factory=db_factory)
                app.state.reflexion_wirer = _rw
                # Make the store itself aware of the factory so lazy recall() hydration works.
                _rw._store._db_factory = db_factory
                # Per-tenant hydration is handled lazily in recall() on first miss.
                logger.info("reflexion_wirer_db_wired")
            except Exception as _rw_exc:
                logger.warning("reflexion_wirer_db_wire_failed", error=str(_rw_exc))

            # Wire DB into VerifierCalibrationStore for cross-restart calibration
            try:
                from app.intelligence.verifier_calibration import (
                    _default_calibration_store as _cal_store,
                )

                _cal_store._db = db_factory
                app.state.calibration_store = _cal_store
                logger.info("verifier_calibration_store_wired")
            except Exception as _cal_exc:
                logger.warning("verifier_calibration_wire_failed", error=str(_cal_exc))

            # Wire DB into ABTestingEngine + hydrate historical results
            try:
                from app.optimization.ab_testing import ab_testing_engine as _ab_engine

                _ab_engine._db_factory = db_factory
                import asyncio as _ab_asyncio

                _ab_asyncio.create_task(_ab_engine.load_from_db(db_factory=db_factory))
                logger.info("ab_testing_engine_wired")
            except Exception as _ab_exc:
                logger.warning("ab_testing_engine_wire_failed", error=str(_ab_exc))

            # Wire Episodic and Procedural memory stores
            try:
                from app.memory.episodic import EpisodicMemoryStore
                from app.memory.procedural import ProceduralMemoryStore

                _episodic_memory = EpisodicMemoryStore(
                    db_factory=db_factory,
                    embedder=app.state.embedder if hasattr(app.state, "embedder") else None,
                )
                _procedural_memory = ProceduralMemoryStore(db_factory=db_factory)
                app.state.episodic_memory = _episodic_memory
                app.state.procedural_memory = _procedural_memory
                logger.info("episodic_procedural_memory_wired")
            except Exception as _ep_exc:
                logger.warning("episodic_procedural_memory_wire_failed", error=str(_ep_exc))

            # Load governance policies from DB into PolicyEngine (H2 fix)
            try:
                from sqlalchemy import text as _sql_text

                async with db_factory() as _pol_session:
                    _pol_result = await _pol_session.execute(
                        _sql_text(
                            "SELECT name, tenant_id, tools_pattern, action, description "
                            "FROM governance_policies"
                        )
                    )
                    _pol_rows = _pol_result.fetchall()
                from app.governance.policies import Policy as _PolicyClass

                for _row in _pol_rows:
                    _pname, _ptenant, _ppattern, _paction, _pdesc = _row
                    _denied = [_ppattern] if _paction == "deny" else []
                    _approval = [_ppattern] if _paction == "require_approval" else []
                    _p = _PolicyClass(
                        name=_pname,
                        description=_pdesc or "",
                        denied_tools=_denied,
                        approval_tools=_approval,
                        tenant_id=_ptenant or "",
                    )
                    _policy_engine._policies.append(_p)
                logger.info("policy_engine_loaded", count=len(_pol_rows))
            except Exception as _pol_exc:
                logger.warning("policy_engine_load_failed", error=str(_pol_exc))

            # Re-wire compliance controller with DB-backed services and DB factory.
            _compliance_controller.configure_services(
                goal_service=_goal_svc_with_db,
                audit_log=_audit_log_db,
                agent_store=_agent_store_with_db,
                schedule_store=_schedule_store_db,
                knowledge_store=_knowledge_store_db,
                db=db_factory,
            )

            # Wire v2 ComplianceChecker with real DB factory (no hardcoded booleans).
            _compliance_checker._db = db_factory
            app.state.compliance_checker = _compliance_checker

            # Wire v2 SelfOptimizer with real DB factory + Redis.
            _self_optimizer_v2._db = db_factory
            if redis_for_runtime is not None:
                _self_optimizer_v2._redis = redis_for_runtime
                _self_optimizer_v2._state = _self_optimizer_v2._state.__class__(redis_for_runtime)
            app.state.self_optimizer_v2 = _self_optimizer_v2

            # ── Wire Redis into runtime services ──────────────────────────────
            if redis_for_runtime is not None:
                # GoalService: Redis pub/sub for cross-replica SSE delivery.
                _goal_svc_with_db._redis = redis_for_runtime
                # Store URL so subscribe_events() can open dedicated pub/sub connections
                # (a single shared Redis client cannot serve multiple blocking subscribers).
                if settings.redis_url:
                    _goal_svc_with_db._redis_url_for_pubsub = str(settings.redis_url)

                # CostController: Redis for cross-replica budget accuracy.
                _cost_ctrl = getattr(app.state, "cost_controller", None)
                if _cost_ctrl is not None and hasattr(_cost_ctrl, "_redis"):
                    _cost_ctrl._redis = redis_for_runtime

                # OAuthFlowManager: DB token persistence.
                _oauth = getattr(app.state, "oauth_manager", None)
                if _oauth is not None and hasattr(_oauth, "_db_session_factory"):
                    _oauth._db_session_factory = db_factory
                    with suppress(Exception):
                        await _oauth.load_tokens_from_db()

                # MCPClient: Redis circuit-breaker + oauth + tool cache.
                _mcp = getattr(app.state, "mcp_client", None)
                if _mcp is not None:
                    if hasattr(_mcp, "_redis"):
                        _mcp._redis = redis_for_runtime
                    if hasattr(_mcp, "_oauth_manager"):
                        _mcp._oauth_manager = getattr(app.state, "oauth_manager", None)
                    # Wire ToolResultCache (created fresh with Redis backend)
                    try:
                        from app.mcp.tool_cache import ToolResultCache as _TRC

                        _tool_cache = _TRC(redis=redis_for_runtime)
                        _mcp._tool_cache = _tool_cache
                        app.state.tool_cache = _tool_cache
                        logger.info("tool_result_cache_wired")
                    except Exception as _tce:
                        logger.warning("tool_result_cache_wire_failed", error=str(_tce))

                # RPA session manager: Redis-backed session registry for restart survival.
                _rpa_sm = getattr(app.state, "rpa_session_manager", None)
                if _rpa_sm is not None:
                    _rpa_sm._redis = redis_for_runtime

                # RPA session store: Redis-backed so sessions survive pod restarts.
                _rpa_ss = getattr(app.state, "rpa_session_store", None)
                if _rpa_ss is not None:
                    _rpa_ss._redis = redis_for_runtime

                # SemanticCache: wire Redis so cache is shared across all workers.
                _sem_cache = getattr(app.state, "semantic_cache", None)
                if _sem_cache is not None and hasattr(_sem_cache, "_redis"):
                    _sem_cache._redis = redis_for_runtime

                # SemanticCache: wire pgvector ANN backend for O(log n) L2 lookup.
                try:
                    from app.rag.vector_cache_backend import select_cache_backend as _scb_select

                    _sem_cache_backend = await _scb_select(
                        db_factory=db_factory,
                        redis=redis_for_runtime,
                    )
                    _sc = getattr(app.state, "semantic_cache", None)
                    if _sc is not None:
                        _sc._backend = _sem_cache_backend
                    logger.info("semantic_cache_backend_wired")
                except Exception as _scb_exc:
                    logger.warning("semantic_cache_backend_wire_failed", error=str(_scb_exc))

                # GoalDeduplicator: wire Redis for cross-replica dedup.
                try:
                    from app.services.dedup import _default_deduplicator as _goal_dedup

                    _goal_dedup._redis = redis_for_runtime
                    logger.info("goal_deduplicator_redis_wired")
                except Exception as _gd_exc:
                    logger.warning("goal_dedup_redis_wire_failed", error=str(_gd_exc))

                # LLMResponseCache: wire Redis for cross-replica LLM cache.
                try:
                    from app.rag.llm_response_cache import LLMResponseCache as _LLMRC

                    _llm_rc = _LLMRC(redis=redis_for_runtime)
                    app.state.llm_response_cache = _llm_rc
                    logger.info("llm_response_cache_wired")
                except Exception as _lrc_exc:
                    logger.warning("llm_response_cache_wire_failed", error=str(_lrc_exc))

                # ── PromptOptimizer: wire Redis for cross-replica cache invalidation ──
                try:
                    from app.intelligence.prompt_optimizer import _default_optimizer as _opt

                    _opt.set_redis(redis_for_runtime)
                    app.state.prompt_optimizer = _opt
                except Exception as _opt_exc:
                    logger.warning("prompt_optimizer_redis_wire_failed", error=str(_opt_exc))

                # ── RedisBulkheadRegistry: distributed per-tenant concurrency ──────
                try:
                    from app.reliability.bulkhead import RedisBulkheadRegistry

                    _bulkhead_registry = RedisBulkheadRegistry(
                        redis=redis_for_runtime,
                        default_max_concurrent=20,
                    )
                    app.state.bulkhead_registry = _bulkhead_registry
                    logger.info("redis_bulkhead_registry_wired")
                except Exception as _bh_exc:
                    logger.warning("bulkhead_registry_wire_failed", error=str(_bh_exc))

                # ── Policy pub/sub: propagate policy changes to all replicas ────────
                try:
                    from app.governance.policies import start_policy_subscriber

                    app.state._policy_pubsub_redis = redis_for_runtime
                    app.state._policy_pubsub_task = start_policy_subscriber(
                        redis_url=str(settings.redis_url),
                        engine=_policy_engine,
                        db=db_factory,
                    )
                    logger.info("policy_pubsub_subscriber_started")
                except Exception as _ps_exc:
                    logger.warning("policy_pubsub_start_failed", error=str(_ps_exc))

                # ── IdempotencyStore: prevent duplicate goal submissions ────────────
                try:
                    from app.reliability.idempotency import IdempotencyStore as _IdempotencyStore

                    _idem_store = _IdempotencyStore(redis=redis_for_runtime)
                    app.state.idempotency_store = _idem_store
                    logger.info("idempotency_store_wired")
                except Exception as _idem_exc:
                    logger.warning("idempotency_store_wire_failed", error=str(_idem_exc))

                # ── AgentIdentityService: upgrade Redis for JWKS cache invalidation ──
                if hasattr(_agent_identity_svc, "set_redis"):
                    _agent_identity_svc.set_redis(redis_for_runtime)
                    logger.info("agent_identity_service_redis_wired")

                # ── AuditV3: wire DB+Redis backed audit chain ────────────────────────
                try:
                    import asyncio as _asyncio_wal

                    from app.governance.audit_v3 import (
                        AuditFlusher as _AuditFlusher,
                    )
                    from app.governance.audit_v3 import (
                        AuditWriter as _AuditWriter,
                    )

                    _audit_writer = _AuditWriter(redis=redis_for_runtime)
                    app.state.audit_writer = _audit_writer
                    _audit_flusher = _AuditFlusher(redis=redis_for_runtime, db_factory=db_factory)
                    _flush_task = _asyncio_wal.create_task(_audit_flusher.run())
                    logger.info("audit_v3_wired")
                except Exception as _aw_exc:
                    logger.warning("audit_v3_wire_failed", error=str(_aw_exc))

                # ── GuardrailEngine v2: wire Redis for tenant config cache ────────────
                _guardrail_engine_v2._redis = redis_for_runtime
                logger.info("guardrail_engine_v2_redis_wired")

                # ── G-19: OrgEventPublisher — publish org.approval.* SSE events ────
                # Without this, OrgRealtimeManager's APPROVAL_REQUESTED/GRANTED/REJECTED/TIMEOUT
                # case handlers never fire (the backend never publishes to Redis pub/sub).
                try:
                    from app.org.events import (
                        configure_org_event_publisher,
                        get_org_event_publisher,
                    )

                    _notif_router = getattr(app.state, "notification_router", None)
                    configure_org_event_publisher(
                        redis_client=redis_for_runtime,
                        audit_service=getattr(app.state, "audit_log", None),
                        notification_router=_notif_router,
                    )
                    app.state.org_event_publisher = get_org_event_publisher()
                    logger.info("org_event_publisher_wired")
                except Exception as _oep_exc:
                    logger.warning("org_event_publisher_wire_failed", error=str(_oep_exc))

                # ── G-20: ApprovalChainEngine — Redis-backed persistence ──────────
                # Persist cross-department approval requests across replicas + restarts.
                try:
                    from app.org.approval_chain import get_approval_engine

                    _approval_engine = get_approval_engine()
                    _approval_engine.set_redis(redis_for_runtime)
                    app.state.approval_chain_engine = _approval_engine
                    logger.info("approval_chain_engine_redis_wired")
                except Exception as _ace_exc:
                    logger.warning("approval_chain_engine_wire_failed", error=str(_ace_exc))

                # ── CRDT manager: wire Redis for multi-process Yjs sync ───────────────
                try:
                    from app.api.collab import _crdt_manager

                    _crdt_manager.set_redis(redis_for_runtime)
                    app.state._redis = redis_for_runtime
                    logger.info("crdt_manager_redis_wired")
                except Exception as _crdt_exc:
                    logger.warning("crdt_manager_redis_wire_failed", error=str(_crdt_exc))

                # ── Observability log store: wire Redis Streams backend ───────────────
                try:
                    from app.api.observability import log_store as _obs_log_store

                    _obs_log_store.set_redis(redis_for_runtime)
                    logger.info("observability_log_store_wired_to_redis")
                except Exception as _obs_exc:
                    logger.warning("observability_log_store_wire_failed", error=str(_obs_exc))

            # ── PromptOptimizer: load variants from DB (all replicas on startup) ──
            try:
                from app.intelligence.prompt_optimizer import _default_optimizer as _opt_db

                loaded_variants = await _opt_db.load_from_db(db_factory)
                logger.info("prompt_variants_loaded_from_db", count=loaded_variants)
            except Exception as _pv_exc:
                logger.warning("prompt_variants_load_failed", error=str(_pv_exc))

            # ── HITLGateway: restore pending approvals from DB on startup ──────────
            try:
                _hitl_restored = await _hitl.startup_restore(db=db_factory)
                logger.info("hitl_startup_restore_complete", count=_hitl_restored)
            except Exception as _hitl_exc:
                logger.warning("hitl_startup_restore_failed", error=str(_hitl_exc))

            # Wire db_session_factory so new runtime approval requests are persisted
            _hitl._db_session_factory = db_factory

            # ── C-1: Start Celery→SSE event bridge when Redis is available ────────
            if redis_for_runtime is not None and settings.redis_url:
                _bridge_svc = getattr(app.state, "goal_service", None)
                if _bridge_svc is not None and hasattr(_bridge_svc, "start_celery_event_bridge"):
                    try:
                        _bridge_svc.start_celery_event_bridge(str(settings.redis_url))
                        logger.info("celery_event_bridge_started")
                    except Exception as _bridge_exc:
                        logger.warning("celery_event_bridge_start_failed", error=str(_bridge_exc))

            # ── H-3: Seed RBAC scope definitions from declarative registry ───────
            try:
                from app.auth.scope_seeder import seed_builtin_scopes

                await seed_builtin_scopes(db_factory)
                logger.info("scope_seeder_complete")
            except Exception as _seed_exc:
                logger.warning("scope_seeder_failed", error=str(_seed_exc))

            # ── M-3: Warm permission cache for recently-active tenants ────────────
            if redis_for_runtime is not None:
                try:
                    import asyncio as _asyncio_cw

                    from app.auth.cache_warmer import warm_permission_cache

                    _asyncio_cw.create_task(
                        warm_permission_cache(redis=redis_for_runtime, db_factory=db_factory)
                    )
                    logger.info("permission_cache_warming_started")
                except Exception as _cw_exc:
                    logger.warning("permission_cache_warm_failed", error=str(_cw_exc))

            # ── H-4: Wire SIEM adapter if configured ──────────────────────────────
            try:
                import os as _os_siem

                _siem_adapter = None
                if _os_siem.getenv("SIEM_TYPE") or settings.siem_type:
                    from app.governance.siem_adapters import (
                        SIEMConfig,
                        SIEMType,
                        build_siem_adapter,
                    )

                    _siem_type_str = _os_siem.getenv("SIEM_TYPE") or settings.siem_type
                    try:
                        _siem_cfg = SIEMConfig(
                            siem_type=SIEMType(_siem_type_str),
                            endpoint=_os_siem.getenv("SIEM_ENDPOINT") or settings.siem_endpoint,
                            credentials={
                                "token": _os_siem.getenv("SIEM_TOKEN") or settings.siem_token,
                                "api_key": _os_siem.getenv("SIEM_API_KEY") or settings.siem_api_key,
                            },
                        )
                        _siem_adapter = build_siem_adapter(_siem_cfg)
                    except Exception as _siem_init_exc:
                        logger.warning("siem_adapter_init_failed", error=str(_siem_init_exc))
                app.state.siem_adapter = _siem_adapter
                if _siem_adapter:
                    logger.info("siem_adapter_registered", siem_type=_siem_type_str)
            except Exception as _siem_exc:
                logger.warning("siem_adapter_setup_failed", error=str(_siem_exc))
                app.state.siem_adapter = None

            # ── H-5: Wire LegalHoldManager with DB + Redis ────────────────────────
            try:
                from app.governance.legal_holds import LegalHoldManager

                _lhm = LegalHoldManager(redis=redis_for_runtime, db_factory=db_factory)
                app.state.legal_hold_manager = _lhm
                logger.info("legal_hold_manager_wired")
            except Exception as _lhm_exc:
                logger.warning("legal_hold_manager_wire_failed", error=str(_lhm_exc))

            # Orchestration persistence: hydrate tool trust from DB (always-on, no flag gate)
            try:
                from app.services.orchestration_persistence import OrchestrationPersistence

                _orch_persistence = OrchestrationPersistence(db=db_factory)
                import asyncio as _asyncio

                _asyncio.create_task(_orch_persistence.load_tool_trust_from_db("*", db=db_factory))
                app.state.orchestration_persistence = _orch_persistence
                logger.info("orchestration_persistence_hydration_started")
            except Exception as _orch_exc:
                logger.warning("orchestration_state_hydration_failed", error=str(_orch_exc))

            try:
                yield
            finally:
                await close_retrieval_gateways()
                await close_process_rerankers()
                _repo_tasks = list(getattr(app.state, "repository_ingestion_tasks", set()))
                for _repo_task in _repo_tasks:
                    _repo_task.cancel()
                if _repo_tasks:
                    import asyncio as _repo_asyncio

                    await _repo_asyncio.gather(*_repo_tasks, return_exceptions=True)
                if _ps_task := getattr(app.state, "_policy_pubsub_task", None):
                    _ps_task.cancel()
                    import contextlib

                    with contextlib.suppress(Exception):
                        await _ps_task
                await active.shutdown()
        else:
            try:
                yield
            finally:
                await close_retrieval_gateways()
                await close_process_rerankers()

        # ── Voice OS warmup (non-blocking — loads STT/TTS providers in background)
        if getattr(settings, "voice_enabled", True):
            try:
                import asyncio as _voice_asyncio

                from app.voice.providers import warmup_providers as _voice_warmup

                _voice_asyncio.create_task(_voice_warmup())
                logger.info("voice_providers_warmup_scheduled")
                # D-6: Start proactive voice alert manager
                from app.voice.alerts import VoiceAlertManager as _VAM

                _alert_mgr = _VAM(redis=getattr(app.state, "redis", None))
                await _alert_mgr.start()
                app.state.voice_alert_manager = _alert_mgr
                logger.info("voice_alert_manager_started")
            except Exception as _voice_exc:
                logger.warning("voice_providers_warmup_skipped", error=str(_voice_exc))

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    async def _resolve_connector_secret(ref: str, tenant_ctx: Any = None) -> str | None:
        store = getattr(app.state, "connector_secret_store", None)
        return await resolve_connector_secret_ref_for_tenant(
            ref,
            store=store,
            tenant_ctx=tenant_ctx,
        )

    def _make_mcp_client(registry: MCPRegistry) -> MCPClient:
        return MCPClient(registry=registry, secret_resolver=_resolve_connector_secret)

    _mcp_client = _make_mcp_client(_mcp_registry)

    # Wire MCP client into tool inverse registry so rollback inverses can execute real API calls
    from app.reliability.tool_inverses import set_mcp_client as _set_inverse_mcp_client

    _set_inverse_mcp_client(_mcp_client)

    # ── Bind all services to app.state ────────────────────────────────────────
    app.state.settings = settings
    app.state.manage_pools = manage_pools
    app.state.health = registry
    # Respect a pre-configured embedder (e.g. injected by tests) rather than
    # overwriting it with None when no real provider API key is available.
    if getattr(app.state, "embedder", None) is None:
        app.state.embedder = _embedder
    app.state.model_router = _model_router
    # Core services
    app.state.tenant_service = _tenant_svc
    app.state.goal_service = _goal_svc
    from app.orchestration.graph_factory import GraphFactory
    from app.orchestration.strategy_certification import CertificationEvaluator
    from app.orchestration.strategy_readiness import ReadinessEvaluator
    from app.orchestration.strategy_registry import build_default_registry
    from app.orchestration.strategy_runner import StrategyRunner

    app.state.strategy_registry = build_default_registry()
    app.state.strategy_readiness = ReadinessEvaluator()
    app.state.strategy_certification = CertificationEvaluator()
    app.state.strategy_runner = StrategyRunner(app.state.strategy_registry)
    app.state.graph_factory = GraphFactory()
    from app.routing_runtime.decision_store import InMemoryDecisionStore
    from app.routing_runtime.embedding_router import EmbeddingRouter as CanonicalEmbeddingRouter
    from app.routing_runtime.model_router import ModelRouter as CanonicalModelRouter
    from app.routing_runtime.skill_router import SkillRouter as CanonicalSkillRouter

    app.state.routing_decision_store = InMemoryDecisionStore()
    app.state.canonical_model_router = CanonicalModelRouter(
        decision_store=app.state.routing_decision_store
    )
    app.state.canonical_skill_router = CanonicalSkillRouter(
        decision_store=app.state.routing_decision_store
    )
    app.state.canonical_embedding_router = CanonicalEmbeddingRouter(
        decision_store=app.state.routing_decision_store
    )
    from app.memory.repository import InMemoryMemoryRepository

    app.state.memory_repository = InMemoryMemoryRepository()
    from app.memory.reflexion import ReflexionService

    app.state.reflexion_service = ReflexionService(repository=app.state.memory_repository)
    from app.intelligence.improvement_action_executor import ImprovementActionExecutor
    from app.intelligence.learning_experiments import LearningExperimentService
    from app.memory.prospective import ProspectiveMemoryService

    app.state.prospective_memory_service = ProspectiveMemoryService()
    app.state.learning_experiment_service = LearningExperimentService()
    app.state.improvement_action_executor = ImprovementActionExecutor(handlers={})
    from app.coordination.auction.repository import (
        InMemoryAuctionRepository,
        InMemorySealedBidInbox,
    )
    from app.coordination.camel.repository import InMemoryCamelRepository
    from app.coordination.generative.repository import InMemoryGenerativeRepository
    from app.coordination.handoffs.membership import (
        DatabaseHandoffMembership,
        InMemorySessionAuthorizer,
    )
    from app.coordination.handoffs.repository import InMemoryHandoffRepository
    from app.coordination.handoffs.service import HandoffService
    from app.coordination.ledger.repository import InMemoryProgressLedgerRepository
    from app.coordination.magentic.human_review import MagenticHumanReviewService
    from app.coordination.moa.repository import InMemoryMoARepository
    from app.coordination.service import CoordinationService
    from app.coordination.store import InMemoryCoordinationStore
    from app.coordination.swarm.repository import InMemorySwarmRepository
    from app.coordination.transcript.repository import InMemoryTranscriptRepository
    from app.coordination.transcript.service import TranscriptService

    _transcript_repository = InMemoryTranscriptRepository()
    _coordination_store = InMemoryCoordinationStore()
    app.state.coordination_service = CoordinationService(_coordination_store)
    app.state.transcript_service = TranscriptService(_transcript_repository)
    app.state.handoff_service = HandoffService(
        InMemoryHandoffRepository(),
        membership=DatabaseHandoffMembership(
            lambda: getattr(app.state, "db_session_factory", None)
        ),
    )
    app.state.coordination_session_authorizer = InMemorySessionAuthorizer(_coordination_store)
    app.state.progress_ledger_repository = InMemoryProgressLedgerRepository()
    app.state.moa_repository = InMemoryMoARepository()
    app.state.camel_repository = InMemoryCamelRepository()
    app.state.generative_repository = InMemoryGenerativeRepository()
    app.state.swarm_repository = InMemorySwarmRepository()
    app.state.auction_repository = InMemoryAuctionRepository()
    app.state.auction_bid_inbox = InMemorySealedBidInbox()
    app.state.magentic_human_review = MagenticHumanReviewService()
    app.state._app_provider = _app_provider
    app.state.mcp_registry = _mcp_registry
    app.state.mcp_client = _mcp_client
    app.state.tool_cache = _tool_cache_inmem
    if _mcp_client is not None and _tool_cache_inmem is not None:
        _mcp_client._tool_cache = _tool_cache_inmem
    app.state.oauth_manager = _oauth_manager
    app.state.agent_store = _agent_store
    app.state.meta_agent = _meta_agent
    # ── Phase 3: Agent Router (auto-routes goals when agent_id is omitted) ────
    try:
        from app.agent.router import AgentRouter

        _agent_router = AgentRouter(
            agent_store=_agent_store,
            llm_provider=_app_provider,
        )
        app.state.agent_router = _agent_router
        logger.info("agent_router_registered")
    except Exception as _ar_exc:
        logger.warning("agent_router_init_failed", error=str(_ar_exc))
        app.state.agent_router = None
    # ── Phase 2A: ToolSelector (goal-aware top-k tool retrieval) ─────────────
    try:
        from app.agent.tool_selector import ToolSelector
        from app.mcp.capability_search import CapabilitySearch

        _capability_search = CapabilitySearch(embedder=_embedder)
        _tool_selector = ToolSelector(capability_search=_capability_search)
        app.state.tool_selector = _tool_selector
        logger.info("tool_selector_registered")
    except Exception as _ts_exc:
        logger.warning("tool_selector_init_failed", error=str(_ts_exc))
        app.state.tool_selector = None
    # Governance
    app.state.hitl_gateway = _hitl
    app.state.audit_log = _audit_log
    app.state.cost_controller = _cost
    app.state.policy_engine = _policy_engine
    # Scheduling
    app.state.schedule_store = _schedule_store
    app.state.nl_scheduler = _nl_sched
    # Knowledge + Memory
    app.state.knowledge_store = _knowledge_store
    app.state.repository_ingestion_tasks = set()
    # Ingestion framework
    app.state.ingestion_pipeline = _ingestion_pipeline
    app.state.ingestion_job_tracker = _ingestion_job_tracker
    app.state.retrieval_gateway = _retrieval_gateway
    app.state.raft_service = _raft_service
    app.state.safe_web_search_capability = _web_search_capability
    app.state.semantic_cache = _semantic_cache
    app.state.long_term_memory = _long_term_memory
    # H-3: ExecutionMemory on app.state
    app.state.exec_memory = _exec_memory
    # Cost Tracker
    app.state.cost_tracker = _cost_tracker
    # Agent Identity Service (JWT service-account credentials)
    app.state.agent_identity_service = _agent_identity_svc
    # GuardrailEngine v2 (six-layer input/output guardrails)
    app.state.guardrail_engine = _guardrail_engine_v2
    # Usage Metering (Phase 1e billing)
    _usage_service = UsageService()
    app.state.usage_service = _usage_service
    # Intelligence
    app.state.eval_runner = _eval_runner
    app.state.eval_suite_runner = _eval_suite_runner
    app.state.self_optimizer = _self_optimizer
    app.state.self_optimizer_v2 = _self_optimizer_v2
    # PromptOptimizer: always set in-memory default (upgraded with Redis in lifespan)
    from app.intelligence.prompt_optimizer import _default_optimizer as _prompt_optimizer_default

    app.state.prompt_optimizer = _prompt_optimizer_default
    # Enterprise
    app.state.compliance_controller = _compliance_controller
    app.state.compliance_checker = _compliance_checker  # v2: no hardcoded booleans
    app.state.simulation_runner = _simulation_runner
    app.state.red_team_runner = _red_team_runner
    app.state.marketplace = _marketplace
    app.state.marketplace_v2 = _marketplace_v2
    app.state.collab_store = CollaborationStore()
    # RPA
    app.state.rpa_executor = _rpa_executor
    app.state.rpa_session_store = _rpa_session_store
    app.state.rpa_session_manager = _rpa_session_manager
    app.state.rpa_artifact_store = _rpa_artifact_store
    # Notifications
    app.state.notification_service = _notification_service
    # Wire notification service into HITL gateway for approval alerts
    _hitl._notification_service = _notification_service
    # Perception
    app.state.browser_agent = _browser_agent
    app.state.page_analyzer = _page_analyzer
    # Workflow Builder (legacy canvas-based)
    app.state.workflow_store = WorkflowStore()
    # Goal Templates
    app.state.template_store = _template_store

    # ── Workflow Automation Engine ────────────────────────────────────────────
    try:
        from app.workflow.compiler import WorkflowCompiler
        from app.workflow.context import ContextResolver
        from app.workflow.hitl_extension import HITLWorkflowGateway
        from app.workflow.nl_trigger import NLTriggerResolver
        from app.workflow.runner import WorkflowRunner
        from app.workflow.template_store import SystemTemplateStore

        _wf_ctx = ContextResolver()
        _wf_compiler = WorkflowCompiler(context_resolver=_wf_ctx)
        _wf_runner = WorkflowRunner(compiler=_wf_compiler)
        _hitl_wf_gateway = HITLWorkflowGateway()
        _nl_trigger_resolver = NLTriggerResolver()
        _system_template_store = SystemTemplateStore()

        # workflow_service: wraps workflow_store with full router-compatible interface
        from app.workflow.service import WorkflowService as _WorkflowService

        app.state.workflow_service = _WorkflowService(app.state.workflow_store)
        app.state.workflow_runner = _wf_runner
        app.state.workflow_compiler = _wf_compiler
        app.state.hitl_workflow_gateway = _hitl_wf_gateway
        app.state.nl_trigger_resolver = _nl_trigger_resolver
        app.state.template_store_we = _system_template_store  # workflow engine templates
    except Exception as _wfe:
        import logging as _log

        _log.getLogger(__name__).warning("workflow_engine_state_init_failed: %s", _wfe)

    # ── Middleware (order matters — outermost wraps last) ─────────────────────
    # NOTE: CORSMiddleware must be outermost (added LAST) so CORS headers are
    # present on ALL responses including 403s from ScopeEnforcementMiddleware.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(ScopeEnforcementMiddleware)

    # Dynamic resolver reads from app.state so lifespan can swap the service
    # (e.g. after wiring the DB session factory) without breaking auth.
    async def _dynamic_resolver(raw_key: str) -> Any:
        """Resolve using app.state.tenant_service — updated by lifespan if DB available."""
        svc = app.state.tenant_service
        return await svc.resolve_api_key(raw_key)

    app.state._tenant_key_resolver = _dynamic_resolver

    app.add_middleware(
        TenantMiddleware,
        key_resolver=_dynamic_resolver,
        rate_limiter=_fake_redis,
    )
    # CORSMiddleware is outermost — added last so its CORS headers wrap ALL
    # responses including error 403s from ScopeEnforcementMiddleware.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Wire app reference into GoalService for per-tenant LLM provider dispatch.
    _goal_svc._app_state = app

    # ── Isolated Agent Execution Environment ──────────────────────────────────
    # Always registers a FakeRunner-backed scheduler (default off).  This ensures
    # getattr(app.state, "execution_scheduler") is never None — callers won't get
    # an AttributeError even when the isolation flag is off.
    try:
        from app.core.runtime_flags import get_runtime_flags as _get_rtflags
        from app.execution_environment.scheduler import ExecutionEnvironmentScheduler

        _iso_flags = _get_rtflags()
        app.state.execution_scheduler = ExecutionEnvironmentScheduler.from_flags(
            isolated_execution_local_runner=_iso_flags.isolated_execution_local_runner,
            isolated_execution_kubernetes_runner=_iso_flags.isolated_execution_kubernetes_runner,
        )
    except Exception as _iso_init_exc:
        import logging as _iso_log

        _iso_log.getLogger(__name__).error(
            "execution_scheduler_init_failed: %s — using default FakeRunner scheduler",
            _iso_init_exc,
        )
        # Even on failure, register a safe FakeRunner scheduler so
        # isolated execution attempts fail with a structured RunnerUnavailableError
        # instead of an AttributeError on None.
        try:
            from app.execution_environment.scheduler import (
                ExecutionEnvironmentScheduler as _ESFallback,
            )

            app.state.execution_scheduler = _ESFallback()
        except Exception:
            app.state.execution_scheduler = None  # last resort

    # ── Error handlers ────────────────────────────────────────────────────────
    _register_error_handlers(app)

    # ── Routers ────────────────────────────────────────────────────────────
    # All router imports and include_router() calls are in app/bootstrap/routers.py
    from app.bootstrap.routers import register_routers

    register_routers(app, settings, logger)

    configure_tracing(settings.service_name, settings.otel_exporter_otlp_endpoint)

    return app


app = create_app(manage_pools=True)
