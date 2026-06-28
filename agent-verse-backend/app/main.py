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

import builtins
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.a2a import router as a2a_router
from app.api.analytics import router as analytics_router
from app.api.civilization import router as civilization_router
from app.api.costs import router as costs_router
from app.api.workflows import _WorkflowStore as WorkflowStore
from app.api.workflows import router as workflows_router
from app.api.insights import router as insights_router
from app.api.templates import router as templates_router, template_store as _template_store
from app.api.replay import router as replay_router
from app.api.training_export import router as training_export_router
from app.api.integrations import router as integrations_router
from app.api.agents import AgentStore
from app.api.agents import router as agents_router
from app.api.artifacts import router as artifacts_router
from app.api.auth import router as auth_router
from app.api.collab import router as collab_router
from app.api.connectors import router as connectors_router
from app.api.enterprise import (
    intelligence_router,
    marketplace_router,
    compliance_router,
    scim_router,
)
from app.api.enterprise import (
    router as enterprise_router,
)
from app.api.goals import router as goals_router
from app.api.governance import router as governance_router
from app.api.knowledge import router as knowledge_router
from app.api.memory import router as memory_router
from app.api.perception import router as perception_router
from app.api.rpa import router as rpa_router
from app.api.schedules import (
    events_router,
    nl_router,
    webhooks_router,
)
from app.api.schedules import (
    router as schedules_router,
)
from app.api.system import router as system_router
from app.api.tenants import router as tenants_router
from app.api.tools import router as tools_router
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
from app.intelligence.meta_agent import MetaAgentPlanner
from app.intelligence.self_optimization import SelfOptimizer
from app.intelligence.self_optimizer_v2 import SelfOptimizerV2
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
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.rpa.artifacts import get_artifact_store
from app.rpa.session_manager import BrowserSessionManager
from app.services.event_store import EventStore
from app.services.goal_queue import CeleryGoalTaskQueue
from app.services.goal_service import GoalService
from app.services.notification_service import NotificationService
from app.services.tenant_service import TenantService
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware
from app.auth.scope_enforcement import ScopeEnforcementMiddleware
from app.triggers.nl_scheduler import NLScheduler
from app.triggers.store import ScheduleStore

logger = get_logger(__name__)


def _resolve_provider_for_app(settings: "Settings") -> Any:
    """Resolve a real LLM provider from environment, or FakeProvider as last resort."""
    import os
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")

    if anthropic_key:
        try:
            from app.providers.anthropic_provider import AnthropicProvider
            return AnthropicProvider(api_key=anthropic_key)
        except Exception:
            pass

    if openai_key:
        try:
            from app.providers.openai_compatible import OpenAICompatibleProvider
            return OpenAICompatibleProvider(api_key=openai_key)
        except Exception:
            pass

    # No real provider — warn and fall back to Fake
    logger.warning(
        "no_real_llm_provider_for_meta_services",
        message=(
            "MetaAgentPlanner and NLScheduler are using FakeProvider. "
            "Set ANTHROPIC_API_KEY or OPENAI_API_KEY for real NL→agent and NL→schedule parsing."
        )
    )
    env = os.getenv("ENVIRONMENT", "development").lower()
    if env == "production":
        raise RuntimeError(
            "FATAL: No LLM provider configured for production. "
            "Set ANTHROPIC_API_KEY or OPENAI_API_KEY environment variable."
        )
    logger.warning(
        "fake_provider_active_dev_only",
        message=(
            "FakeProvider is active. "
            "Set ANTHROPIC_API_KEY or OPENAI_API_KEY for real goal execution."
        ),
    )
    from app.providers.fake import FakeProvider
    return FakeProvider(responses=[
        '{"steps": ["Complete the requested task"]}',
        "Task executed successfully",
        '{"success": true, "reason": "Goal achieved"}',
    ])


# ── Minimal in-memory Redis fallback (used when pools are not started) ─────────

class _FakeRedis:
    """Thread/async-safe dict-backed Redis stub — for tests and no-pool mode.

    Supports string ops, set ops, and sorted-set ops required by
    :class:`~app.tenancy.rate_limiter.SlidingWindowRateLimiter`.
    """

    def __init__(self) -> None:
        self._d: dict[str, Any] = {}
        self._s: dict[str, set[str]] = {}
        # sorted sets: key → {member: score}
        self._z: dict[str, dict[str, float]] = {}
        # TTL tracking: key → expiry epoch (monotonic seconds)
        self._ttl: dict[str, float] = {}

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
        zset = self._z.setdefault(key, {})
        added = sum(1 for m in mapping if m not in zset)
        zset.update(mapping)
        return added

    async def zremrangebyscore(
        self, key: str, min_score: float, max_score: float
    ) -> int:
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
        if self._is_expired(key):
            self._z.pop(key, None)
            self._ttl.pop(key, None)
            return 0
        return len(self._z.get(key, {}))

    async def expire(self, key: str, seconds: int) -> bool:
        import time
        self._ttl[key] = time.monotonic() + seconds
        return key in self._d or key in self._z


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

    registry = HealthRegistry(list(health_checks or []))

    # ── Build shared services ─────────────────────────────────────────────────
    _tenant_svc = tenant_service or TenantService()
    _audit_log = AuditLog()
    _hitl = HITLGateway()
    _cost = CostController()
    _policy_engine = PolicyEngine()
    _agent_store = AgentStore()
    _app_provider = _resolve_provider_for_app(settings)
    _meta_agent = MetaAgentPlanner(provider=_app_provider)
    _schedule_store = ScheduleStore()
    _nl_sched = NLScheduler(provider=_app_provider)
    _knowledge_store = KnowledgeStore()
    _semantic_cache = SemanticCache()
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

    # Wire embedder: use VoyageProvider if VOYAGE_API_KEY set,
    # OpenAICompatibleProvider if OPENAI_API_KEY set,
    # LocalEmbedProvider if SENTENCE_TRANSFORMERS_MODEL set, else None.
    import os
    _embedder: Any = None
    _openai_key = os.getenv("OPENAI_API_KEY", "")
    _voyage_key = os.getenv("VOYAGE_API_KEY", "")
    _anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
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
    elif os.getenv("GOOGLE_API_KEY", ""):
        try:
            from app.providers.gemini_provider import GeminiProvider
            _embedder = GeminiProvider(api_key=os.getenv("GOOGLE_API_KEY"))
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

    _browser_agent = BrowserAgent(
        vision_provider=_embedder if _supports_vision else None
    )
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
                # Wire RedisSaver checkpointer for persistent LangGraph state (Fix 7)
                try:
                    from langgraph.checkpoint.redis.aio import AsyncRedisSaver
                    _checkpointer = AsyncRedisSaver.from_conn_string(str(settings.redis_url))
                    await _checkpointer.setup()  # create Redis data structures
                    app.state.langgraph_checkpointer = _checkpointer
                    logger.info("async_redis_saver_checkpointer_wired")
                except (ImportError, Exception) as _exc:
                    # Fall back to sync RedisSaver
                    try:
                        from langgraph.checkpoint.redis import RedisSaver
                        _checkpointer = RedisSaver.from_conn_string(str(settings.redis_url))
                        app.state.langgraph_checkpointer = _checkpointer
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
                        import redis.asyncio as aioredis
                        _aioredis: Any = aioredis
                        _direct_redis: Any = _aioredis.from_url(
                            _redis_url, decode_responses=True
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
                        logger.warning(
                            "Could not connect to Redis for rate limiter: %s", exc
                        )

            # Wire DB session factory into services so they persist to PostgreSQL.
            from app.db.session import get_session_factory

            db_factory = get_session_factory()
            event_store = EventStore(db_factory)

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

            # H-3: Wire DB into ExecutionMemory for persistence
            _exec_memory._db = db_factory
            app.state.exec_memory = _exec_memory

            # Wire DB into CostTracker for ledger persistence + historical queries
            _cost_tracker._db = db_factory
            app.state.cost_tracker = _cost_tracker

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
            await _knowledge_store_db.sync_from_db()

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

                # CostController: Redis for cross-replica budget accuracy.
                _cost_ctrl = getattr(app.state, "cost_controller", None)
                if _cost_ctrl is not None and hasattr(_cost_ctrl, "_redis"):
                    _cost_ctrl._redis = redis_for_runtime

                # OAuthFlowManager: DB token persistence.
                _oauth = getattr(app.state, "oauth_manager", None)
                if _oauth is not None and hasattr(_oauth, "_db_session_factory"):
                    _oauth._db_session_factory = db_factory
                    try:
                        await _oauth.load_tokens_from_db()
                    except Exception:
                        pass

                # MCPClient: Redis circuit-breaker + oauth.
                _mcp = getattr(app.state, "mcp_client", None)
                if _mcp is not None:
                    if hasattr(_mcp, "_redis"):
                        _mcp._redis = redis_for_runtime
                    if hasattr(_mcp, "_oauth_manager"):
                        _mcp._oauth_manager = getattr(app.state, "oauth_manager", None)

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

            # ── C-1: Start Celery→SSE event bridge when Redis is available ────────
            if redis_for_runtime is not None and settings.redis_url:
                _bridge_svc = getattr(app.state, "goal_service", None)
                if _bridge_svc is not None and hasattr(_bridge_svc, "start_celery_event_bridge"):
                    try:
                        _bridge_svc.start_celery_event_bridge(str(settings.redis_url))
                        logger.info("celery_event_bridge_started")
                    except Exception as _bridge_exc:
                        logger.warning("celery_event_bridge_start_failed", error=str(_bridge_exc))

            try:
                yield
            finally:
                # Cancel pub/sub background task on shutdown
                if _ps_task := getattr(app.state, "_policy_pubsub_task", None):
                    _ps_task.cancel()
                    import contextlib
                    with contextlib.suppress(Exception):
                        await _ps_task
                await active.shutdown()
        else:
            yield

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
    app.state.embedder = _embedder
    app.state.model_router = _model_router
    # Core services
    app.state.tenant_service = _tenant_svc
    app.state.goal_service = _goal_svc
    app.state._app_provider = _app_provider
    app.state.mcp_registry = _mcp_registry
    app.state.mcp_client = _mcp_client
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
    app.state.semantic_cache = _semantic_cache
    app.state.long_term_memory = _long_term_memory
    # H-3: ExecutionMemory on app.state
    app.state.exec_memory = _exec_memory
    # Cost Tracker
    app.state.cost_tracker = _cost_tracker
    # Intelligence
    app.state.eval_runner = _eval_runner
    app.state.eval_suite_runner = _eval_suite_runner
    app.state.self_optimizer = _self_optimizer
    app.state.self_optimizer_v2 = _self_optimizer_v2
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
    # Workflow Builder
    app.state.workflow_store = WorkflowStore()
    # Goal Templates
    app.state.template_store = _template_store

    # ── Middleware (order matters — outermost wraps last) ─────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
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

    # Wire app reference into GoalService for per-tenant LLM provider dispatch.
    _goal_svc._app_state = app

    # ── Error handlers ────────────────────────────────────────────────────────
    _register_error_handlers(app)

    # ── Routers ───────────────────────────────────────────────────────────────
    # Core
    app.include_router(system_router)
    app.include_router(tenants_router)
    app.include_router(goals_router)
    app.include_router(connectors_router)
    # Native tools (code execution, file ops, email)
    app.include_router(tools_router)
    # SSO authentication
    app.include_router(auth_router)
    # Agents, governance, knowledge, scheduling
    app.include_router(agents_router)
    app.include_router(governance_router)
    app.include_router(knowledge_router)
    app.include_router(rpa_router)
    app.include_router(schedules_router)
    app.include_router(nl_router)
    app.include_router(webhooks_router)
    app.include_router(events_router)
    # Memory + Artifacts
    app.include_router(memory_router)
    app.include_router(artifacts_router)
    # A2A + collaboration
    app.include_router(a2a_router)
    app.include_router(collab_router)
    # Enterprise + marketplace + intelligence
    app.include_router(enterprise_router)
    app.include_router(marketplace_router)
    app.include_router(intelligence_router)
    # P2.10: Async GDPR export + consent management
    app.include_router(compliance_router)
    # SCIM 2.0 provisioning (mounted at /scim/v2)
    app.include_router(scim_router)
    # Perception
    app.include_router(perception_router)
    # Integrations (Slack, Zapier, email triggers)
    app.include_router(integrations_router)
    # Analytics
    app.include_router(analytics_router)
    # Replay (goal execution timeline)
    app.include_router(replay_router)
    # Training data export (intelligence)
    app.include_router(training_export_router)
    # Civilization (Agent Civilization — feature-flagged at request time)
    app.include_router(civilization_router)
    logger.info("civilization_router_registered")
    # Visual Workflow Builder
    app.include_router(workflows_router)
    logger.info("workflows_router_registered")
    # Insights & Intelligence
    app.include_router(insights_router)
    logger.info("insights_router_registered")
    # Goal Templates
    app.include_router(templates_router)
    logger.info("templates_router_registered")
    # Cost optimization & monitoring
    app.include_router(costs_router)
    logger.info("costs_router_registered")

    configure_tracing(settings.service_name, settings.otel_exporter_otlp_endpoint)

    return app


app = create_app(manage_pools=True)
