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
from app.enterprise.marketplace import Marketplace
from app.enterprise.red_team import RedTeamRunner
from app.enterprise.simulation import SimulationRunner
from app.governance.audit import AuditLog
from app.governance.cost import CostController
from app.governance.hitl import HITLGateway
from app.governance.policies import PolicyEngine
from app.intelligence.eval_runner import EvalRunner
from app.intelligence.eval_suite import EvalSuiteRunner
from app.intelligence.meta_agent import MetaAgentPlanner
from app.intelligence.self_optimization import SelfOptimizer
from app.mcp.client import MCPClient
from app.mcp.oauth import OAuthFlowManager
from app.mcp.registry import MCPRegistry
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
from app.triggers.nl_scheduler import NLScheduler
from app.triggers.store import ScheduleStore

logger = get_logger(__name__)


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

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._d[key] = value

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
    _fake_provider = FakeProvider(responses=[
        '{"steps": ["Complete the requested task"]}',
        "Task executed successfully",
        '{"success": true, "reason": "Goal achieved"}',
    ])
    _meta_agent = MetaAgentPlanner(provider=_fake_provider)
    _schedule_store = ScheduleStore()
    _nl_sched = NLScheduler(
        provider=FakeProvider(responses=[
            '{"trigger_type": "cron", "cron_expression": "0 9 * * *", "timezone": "UTC"}'
        ])
    )
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
    _self_optimizer = SelfOptimizer()
    _notification_service = NotificationService()

    # Wire embedder: use VoyageProvider if VOYAGE_API_KEY set,
    # OpenAICompatibleProvider if OPENAI_API_KEY set, else None (random fallback).
    import os
    _embedder: Any = None
    _openai_key = os.getenv("OPENAI_API_KEY", "")
    _voyage_key = os.getenv("VOYAGE_API_KEY", "")
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
    # app.state.embedder is set after app = FastAPI(...)

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

    # Wire service references into the compliance controller for data export
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
                # Upgrade rate-limiter from in-memory stub to real Redis so all
                # replicas share one rate-limit counter per tenant.
                app.state._rate_limiter_redis = real_redis
                # Wire LLM config store so Celery workers can read tenant configs.
                from app.services.llm_config_store import LLMConfigStore, set_llm_config_store
                _llm_store = LLMConfigStore(redis_client=real_redis)
                set_llm_config_store(_llm_store)
                app.state.llm_config_store = _llm_store
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

            try:
                yield
            finally:
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

    # ── Bind all services to app.state ────────────────────────────────────────
    app.state.settings = settings
    app.state.manage_pools = manage_pools
    app.state.health = registry
    app.state.embedder = _embedder
    # Core services
    app.state.tenant_service = _tenant_svc
    app.state.goal_service = _goal_svc
    app.state.mcp_registry = _mcp_registry
    app.state.mcp_client = _mcp_client
    app.state.oauth_manager = _oauth_manager
    app.state.agent_store = _agent_store
    app.state.meta_agent = _meta_agent
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
    # Intelligence
    app.state.eval_runner = _eval_runner
    app.state.eval_suite_runner = _eval_suite_runner
    app.state.self_optimizer = _self_optimizer
    # Enterprise
    app.state.compliance_controller = _compliance_controller
    app.state.simulation_runner = _simulation_runner
    app.state.red_team_runner = _red_team_runner
    app.state.marketplace = _marketplace
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

    # ── Middleware (order matters — outermost wraps last) ─────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

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
    # Perception
    app.include_router(perception_router)
    # Integrations (Slack, Zapier, email triggers)
    app.include_router(integrations_router)
    # Analytics
    app.include_router(analytics_router)
    # Training data export (intelligence)
    app.include_router(training_export_router)

    configure_tracing(app, settings)

    return app


app = create_app(manage_pools=True)
