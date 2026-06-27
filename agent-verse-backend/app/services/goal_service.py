"""GoalService — in-memory goal lifecycle management.

Responsible for:
  - Accepting goal submissions and launching AgentLoop as asyncio background tasks
  - Tracking goal status and appending SSE events
  - Fanning out events to all live SSE subscribers via per-goal asyncio.Queue
  - Delegating audit-log queries and HITL approval to governance components
  - Per-tenant LLM provider dispatch (Fix 8): reads encrypted API keys from vault
    and builds real provider instances when a tenant has configured one.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator, Coroutine
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from opentelemetry import trace

from app.observability.logging import get_logger as _get_logger

_svc_logger = _get_logger(__name__)

from app.agent.loop import AgentLoop

# Module-level pause event registry (not a class attr to avoid circular)
_GOAL_PAUSE_EVENTS: dict[str, asyncio.Event] = {}
from app.agent.sanitization import sanitize_event
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.agent.workflow_executor import WorkflowExecutor
from app.agent.workflow_planner import build_static_workflow
from app.core.errors import NotFoundError
from app.governance.audit import AuditLog
from app.governance.hitl import HITLGateway
from app.observability.metrics import record_goal_duration, record_goal_started
from app.providers.fake import FakeProvider
from app.services.goal_queue import GoalTaskQueue
from app.tenancy.context import PlanTier, TenantContext

# Module-level OTel tracer — no-ops cleanly when no exporter is configured.
_tracer = trace.get_tracer(__name__)

# Poison-pill sentinel — placed on a subscriber queue to signal end-of-stream.
_SENTINEL: dict[str, Any] | None = None
_TERMINAL_STATUSES = {GoalStatus.COMPLETE, GoalStatus.FAILED, GoalStatus.CANCELLED}

# TTL for completed/failed/cancelled goals in the in-memory cache.
# They are safe to evict because they are already persisted in the DB.
_COMPLETED_GOAL_TTL_SECONDS = 3600  # 1 hour
_EVICTION_INTERVAL_SECONDS = 60  # evict at most once every 60 seconds


def _monotonic() -> float:
    return time.monotonic()


# ── domain model ──────────────────────────────────────────────────────────────


@dataclass
class GoalRecord:
    """Runtime state for a single submitted goal."""

    goal_id: str
    goal_text: str
    status: GoalStatus
    tenant_id: str
    priority: str
    dry_run: bool
    created_at: str  # ISO-8601
    agent_id: str | None = None
    workflow_mode: str = "single_agent"
    execution_context: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    subscribers: list[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=list)
    started_monotonic: float = field(default_factory=lambda: _monotonic())
    terminal_metrics_recorded: bool = False
    # Timestamp when the goal entered a terminal state (complete/failed/cancelled).
    # Used by _evict_stale_goals() to avoid evicting goals that just finished.
    completed_at: str | None = None


# ── helpers ───────────────────────────────────────────────────────────────────


def _resolve_checkpointer(app_state: Any) -> Any:
    """Safely extract a valid LangGraph checkpointer from app_state.

    Returns None when app_state is absent, the attribute is unset, or the
    value is not a genuine BaseCheckpointSaver (e.g. a test MagicMock).
    """
    if app_state is None:
        return None
    cp = getattr(app_state, "langgraph_checkpointer", None)
    if cp is None:
        return None
    try:
        from langgraph.checkpoint.base import BaseCheckpointSaver
        return cp if isinstance(cp, BaseCheckpointSaver) else None
    except Exception:
        return None


def _make_agent_loop() -> Any:
    """Construct an AgentGraph backed by FakeProvider (no real LLM required)."""
    from app.agent.graph import AgentGraph
    from app.intelligence.guardrails import GuardrailChecker
    from app.reliability.dedup import DeduplicationCache
    from app.reliability.result_processor import ResultProcessor
    from app.reliability.rollback import RollbackEngine

    planner = FakeProvider(responses=['{"steps": ["Complete the requested task"]}'])
    executor = FakeProvider(responses=["Task executed successfully"])
    verifier = FakeProvider(responses=['{"success": true, "reason": "Goal achieved"}'])
    try:
        return AgentGraph(
            planner=planner,
            executor=executor,
            verifier=verifier,
            result_processor=ResultProcessor(),
            dedup_cache=DeduplicationCache(),
            rollback_engine=RollbackEngine(),
            guardrail_checker=GuardrailChecker(),
        )
    except Exception as exc:
        _svc_logger.error(
            "agentgraph_construction_failed",
            error=str(exc),
            exc_info=True,
        )
        raise RuntimeError(
            f"Failed to construct AgentGraph: {exc}. "
            "Check provider configuration (ANTHROPIC_API_KEY or OPENAI_API_KEY)."
        ) from exc


def _fake_provider() -> Any:
    """Return a cycling FakeProvider suitable for use as planner, executor, and verifier."""
    return FakeProvider(
        responses=[
            '{"steps": ["Complete the requested task"]}',
            "Task executed successfully",
            '{"success": true, "reason": "Goal achieved"}',
        ]
    )


# ── service ───────────────────────────────────────────────────────────────────


class GoalService:
    """In-memory goal service.

    Wire as ``app.state.goal_service`` in the application factory.
    Optionally inject ``audit_log`` and ``hitl`` for governance integration.
    Pass ``app_state`` (the FastAPI app) to enable per-tenant LLM provider dispatch.
    Pass ``db_session_factory`` to enable background PostgreSQL persistence.
    """

    def __init__(
        self,
        *,
        audit_log: AuditLog | None = None,
        hitl: HITLGateway | None = None,
        app_state: Any = None,
        db_session_factory: Any = None,
        event_store: Any = None,
        task_queue: GoalTaskQueue | None = None,
    ) -> None:
        self._goals: dict[str, GoalRecord] = {}
        self._audit_log: AuditLog = audit_log or AuditLog()
        self._hitl: HITLGateway = hitl or HITLGateway()
        self._app_state: Any = app_state  # FastAPI app; set by create_app after construction
        self._db: Any = db_session_factory  # async_sessionmaker or None
        self._event_store: Any = event_store
        self._task_queue = task_queue
        self._db_tasks: set[asyncio.Future[None]] = set()
        # Redis client for pub/sub (set by create_app lifespan when manage_pools=True)
        self._redis: Any = None
        # Eval scorecards keyed by goal_id; populated on goal completion.
        self._eval_scores: dict[str, Any] = {}
        # Per-tenant list of completed goal durations (seconds) for latency metrics.
        self._goal_durations: dict[str, list[float]] = {}
        # Time-based eviction: track last eviction timestamp.
        self._last_eviction_time: float = time.monotonic()

    def _track_db_task(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._db_tasks.add(task)
        task.add_done_callback(self._db_tasks.discard)

    # ── memory management ─────────────────────────────────────────────────────

    def _evict_stale_goals(self) -> int:
        """Remove terminal goals older than TTL from the in-memory cache.

        These are already persisted in DB. Evicting from memory is safe
        and prevents unbounded growth in long-running processes.
        """
        from datetime import UTC, datetime, timedelta

        cutoff = datetime.now(UTC) - timedelta(seconds=_COMPLETED_GOAL_TTL_SECONDS)
        terminal = (GoalStatus.COMPLETE, GoalStatus.FAILED, GoalStatus.CANCELLED)
        to_evict: list[str] = []
        for goal_id, record in self._goals.items():
            if record.status in terminal:
                completed_at = getattr(record, "completed_at", None)
                if completed_at and isinstance(completed_at, str):
                    try:
                        dt = datetime.fromisoformat(completed_at.rstrip("Z"))
                        if dt.replace(tzinfo=UTC) < cutoff:
                            to_evict.append(goal_id)
                    except Exception:
                        pass
                # else: no timestamp yet — skip (don't evict; goal might have just completed)
        for gid in to_evict:
            del self._goals[gid]
            _GOAL_PAUSE_EVENTS.pop(gid, None)
        self._sweep_pause_events()
        return len(to_evict)

    async def _evict_async(self) -> int:
        """Async wrapper so eviction runs in the event loop without thread race."""
        return self._evict_stale_goals()

    def _sweep_pause_events(self) -> int:
        """Remove pause events for goals that are no longer tracked."""
        stale = [gid for gid in list(_GOAL_PAUSE_EVENTS.keys()) if gid not in self._goals]
        for gid in stale:
            _GOAL_PAUSE_EVENTS.pop(gid, None)
        return len(stale)

    async def _check_daily_goal_limit_redis(self, tenant_ctx: TenantContext) -> None:
        """Atomic Redis-based daily goal counter — works across all replicas.

        Falls back to in-memory count when Redis is unavailable.
        Raises PlanLimitExceededError (HTTP 429) when the limit is reached.
        """
        from app.tenancy.limits import check_daily_goal_limit

        redis = getattr(self, "_redis", None)
        if redis is None:
            # Fall back to in-memory count (single-process mode)
            today_prefix = datetime.now(UTC).strftime("%Y-%m-%d")
            daily_count = sum(
                1 for r in self._goals.values()
                if r.tenant_id == tenant_ctx.tenant_id
                and r.created_at.startswith(today_prefix)
            )
            check_daily_goal_limit(tenant_ctx, daily_count)
            return

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        key = f"daily_goals:{tenant_ctx.tenant_id}:{today}"

        try:
            # Peek at current count (don't increment yet — increment after validation)
            current = int(await redis.get(key) or 0)
            check_daily_goal_limit(tenant_ctx, current)
            # Passed — increment atomically
            await redis.incr(key)
            # Set TTL to end of day + buffer
            now = datetime.now(UTC)
            end_of_day = datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=UTC)
            ttl = int((end_of_day - now).total_seconds()) + 3600
            await redis.expire(key, ttl)
        except Exception:
            raise

    async def _recover_interrupted_goals(self) -> int:
        """Re-enqueue goals that were executing when the process died.

        Called after sync_from_db() on startup so that goals whose agent task
        was killed by a restart are not silently stuck in a non-terminal state.
        """
        terminal = {
            GoalStatus.COMPLETE,
            GoalStatus.FAILED,
            GoalStatus.CANCELLED,
            GoalStatus.WAITING_HUMAN,
        }
        recovered = 0
        for goal_id, record in list(self._goals.items()):
            if record.status not in terminal and getattr(record, "task", None) is None:
                if self._task_queue is not None:
                    try:
                        self._task_queue.enqueue_goal(
                            goal_id=goal_id,
                            goal_text=record.goal_text,
                            tenant_id=record.tenant_id,
                            priority=getattr(record, "priority", "normal"),
                            dry_run=getattr(record, "dry_run", False),
                            agent_id=record.agent_id,
                            workflow_mode=record.workflow_mode,
                            goal_template="",
                        )
                        record.status = GoalStatus.PLANNING
                        recovered += 1
                    except Exception as exc:
                        _svc_logger.warning(
                            "goal_recovery_failed", goal_id=goal_id, error=str(exc)
                        )
                else:
                    # No task queue — mark as failed so callers know to resubmit
                    record.status = GoalStatus.FAILED
                    record.error_message = (  # type: ignore[attr-defined]
                        "Goal interrupted by process restart. Please resubmit."
                    )
        return recovered

    async def _batch_event_counts(
        self, goal_ids: list[str], tenant_id: str
    ) -> dict[str, int]:
        """Fetch event counts for multiple goals in one DB query.

        Replaces per-goal calls to _event_count_for_response() inside
        list_goals() to avoid N+1 query behaviour.
        """
        if not goal_ids or self._db is None:
            return {}
        try:
            from sqlalchemy import text

            async with self._db() as session:
                result = await session.execute(
                    text(
                        "SELECT goal_id, COUNT(*) as cnt FROM goal_events "
                        "WHERE tenant_id = :tid AND goal_id = ANY(:ids) "
                        "GROUP BY goal_id"
                    ),
                    {"tid": tenant_id, "ids": goal_ids},
                )
                return {row[0]: int(row[1]) for row in result.fetchall()}
        except Exception:
            return {}

    def _make_agent_loop_for_tenant(
        self, tenant_ctx: TenantContext, app_state: Any
    ) -> Any:
        """Build an AgentGraph using the tenant's configured LLM provider AND all
        governance/RAG/memory services from app.state.

        This is the production path — every goal runs with full pipeline.
        """
        import os

        from app.agent.graph import AgentGraph
        from app.intelligence.guardrails import GuardrailChecker
        from app.reliability.dedup import DeduplicationCache
        from app.reliability.result_processor import ResultProcessor
        from app.reliability.rollback import RollbackEngine

        # ── LLM provider (real or fallback) ─────────────────────────────────────
        provider: Any = None

        # 1. Check per-tenant config from app.state
        llm_configs: dict[str, Any] = getattr(app_state, "_llm_configs", {}) if app_state else {}
        tenant_cfg = llm_configs.get(tenant_ctx.tenant_id)
        if tenant_cfg:
            encrypted_key = tenant_cfg.get("encrypted_key", "")
            api_key = ""
            if encrypted_key:
                try:
                    from app.providers.vault import get_vault
                    api_key = get_vault().decrypt(encrypted_key)
                except Exception:
                    pass
            pname = tenant_cfg.get("provider", "")
            if pname == "anthropic" and api_key:
                from app.providers.anthropic_provider import AnthropicProvider
                provider = AnthropicProvider(
                    api_key=api_key,
                    default_model=tenant_cfg.get("default_model", "claude-opus-4-8"),
                )
            elif pname in {"openai", "groq", "together", "azure", "ollama"} and api_key:
                from app.providers.openai_compatible import OpenAICompatibleProvider
                provider = OpenAICompatibleProvider(
                    api_key=api_key,
                    base_url=tenant_cfg.get("base_url"),
                    default_model=tenant_cfg.get("default_model", "gpt-4o"),
                )

        # 2. Fall back to env-var provider
        if provider is None:
            anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
            openai_key = os.getenv("OPENAI_API_KEY", "")
            if anthropic_key:
                try:
                    from app.providers.anthropic_provider import AnthropicProvider
                    provider = AnthropicProvider(api_key=anthropic_key)
                except Exception:
                    pass
            elif openai_key:
                try:
                    from app.providers.openai_compatible import OpenAICompatibleProvider
                    provider = OpenAICompatibleProvider(api_key=openai_key)
                except Exception:
                    pass

        # 3. Final fallback: FakeProvider (with explicit warning)
        if provider is None:
            from app.providers.fake import FakeProvider as _FakeProvider
            provider = _FakeProvider(responses=[
                '{"steps": ["Complete the requested task"]}',
                "Task executed successfully",
                '{"success": true, "reason": "Goal achieved"}',
            ])
            _svc_logger.warning(
                "fake_provider_active",
                message=(
                    "No real LLM provider configured. Goal will use FakeProvider. "
                    "Set ANTHROPIC_API_KEY or OPENAI_API_KEY for real agent execution."
                )
            )

        # ── Pull services from app.state ─────────────────────────────────────────
        audit_log = getattr(app_state, "audit_log", None) if app_state else self._audit_log
        # Prefer RedisCostController (cross-replica) over in-memory CostController
        cost_controller = (
            getattr(app_state, "redis_cost_controller", None)
            or getattr(app_state, "cost_controller", None)
        ) if app_state else None
        hitl_gateway = getattr(app_state, "hitl_gateway", None) if app_state else self._hitl
        knowledge_store = getattr(app_state, "knowledge_store", None) if app_state else None
        long_term_memory = getattr(app_state, "long_term_memory", None) if app_state else None
        eval_runner = getattr(app_state, "eval_runner", None) if app_state else None
        policy_engine = getattr(app_state, "policy_engine", None) if app_state else None
        mcp_client = self._get_mcp_client()

        # ── Extract RAG/routing/intelligence services from app.state ─────────────
        _embedder = getattr(app_state, "embedder", None) if app_state else None
        _semantic_cache = getattr(app_state, "semantic_cache", None) if app_state else None
        _model_router = getattr(app_state, "model_router", None) if app_state else None
        _prompt_optimizer = getattr(app_state, "prompt_optimizer", None) if app_state else None
        _bulkhead_registry = getattr(app_state, "bulkhead_registry", None) if app_state else None
        # Agent-level feature flags — read from agent config when available
        _agent_config: dict[str, Any] = {}

        graph = AgentGraph(
            planner=provider,
            executor=provider,
            verifier=provider,
            audit_log=audit_log,
            cost_controller=cost_controller,
            hitl_gateway=hitl_gateway,
            knowledge_store=knowledge_store,
            long_term_memory=long_term_memory,
            mcp_client=mcp_client,
            eval_runner=eval_runner,
            result_processor=ResultProcessor(),
            dedup_cache=DeduplicationCache(),
            rollback_engine=RollbackEngine(),
            guardrail_checker=GuardrailChecker(),
            policy_engine=policy_engine,
            # RAG / intelligence services
            embedder=_embedder,
            semantic_cache=_semantic_cache,
            model_router=_model_router,
            # Distributed per-tenant concurrency bulkhead
            bulkhead_registry=_bulkhead_registry,
            # Agent feature flags
            enable_cot=_agent_config.get("enable_cot", False),
            enable_reflection=_agent_config.get("enable_reflection", False),
            enable_goal_tree=_agent_config.get("enable_goal_tree", False),
            autonomy_mode=_agent_config.get("autonomy_mode", "bounded-autonomous"),
            # Use RedisSaver when available for cross-replica state persistence (Fix 7)
            checkpointer=_resolve_checkpointer(app_state),
        )
        # Wire attributes that are set externally (not constructor params)
        graph._db_session_factory = self._db
        graph._prompt_optimizer = _prompt_optimizer
        # Wire RPA executor for direct RPA tool dispatch without MCP
        _rpa_exec = getattr(app_state, "rpa_executor", None)
        graph._rpa_executor = _rpa_exec
        return graph

    # ── private helpers ───────────────────────────────────────────────────────

    def _get_record(self, goal_id: str, tenant_ctx: TenantContext) -> GoalRecord:
        """Fetch and tenant-validate a :class:`GoalRecord`."""
        record = self._goals.get(goal_id)
        if record is None or record.tenant_id != tenant_ctx.tenant_id:
            raise NotFoundError(f"Goal not found: {goal_id}")
        return record

    def _get_agent_store(self) -> Any:
        """Return the configured agent store when the application wired one."""
        if self._app_state is None:
            return None
        store = getattr(self._app_state, "agent_store", None)
        if store is not None:
            return store
        state = getattr(self._app_state, "state", None)
        return getattr(state, "agent_store", None) if state is not None else None

    def _get_mcp_client(self) -> Any:
        """Return the configured MCP client when the application wired one."""
        if self._app_state is None:
            return None
        client = getattr(self._app_state, "mcp_client", None)
        if client is not None:
            return client
        state = getattr(self._app_state, "state", None)
        return getattr(state, "mcp_client", None) if state is not None else None

    def _validate_agent_id(self, agent_id: str | None, tenant_ctx: TenantContext) -> None:
        if agent_id is None:
            return
        agent_store = self._get_agent_store()
        if agent_store is None:
            return
        if agent_store.get(agent_id, tenant_ctx=tenant_ctx) is None:
            raise NotFoundError(f"Agent not found: {agent_id}")

    async def _build_tool_context(
        self, agent_id: str | None, tenant_ctx: TenantContext
    ) -> ToolContext:
        # Always include built-in RPA tools so agents can use browser automation
        from app.rpa.tools import RPA_TOOLS
        tools: list[ToolRef] = [
            ToolRef(
                server_id="rpa",
                server_name="rpa",
                name=str(rpa_tool["name"]),
                description=str(rpa_tool["description"]),
                input_schema=dict(rpa_tool.get("input_schema", {})),
            )
            for rpa_tool in RPA_TOOLS
        ]

        if agent_id is None or self._app_state is None:
            return ToolContext(connectors=[], tools=tools)

        agent_store = self._get_agent_store()
        mcp_client = self._get_mcp_client()
        if agent_store is None or mcp_client is None:
            return ToolContext(connectors=[], tools=tools)

        agent: dict[str, Any] | None = agent_store.get(agent_id, tenant_ctx=tenant_ctx)
        if agent is None:
            return ToolContext(connectors=[], tools=tools)

        connector_errors: list[dict[str, str]] = []
        for connector_id in agent.get("connector_ids", []):
            connector_id_str = str(connector_id)
            try:
                discovered = await mcp_client.discover_tools(
                    server_id=connector_id_str, tenant_ctx=tenant_ctx
                )
            except Exception as exc:
                connector_errors.append(
                    {"connector_id": connector_id_str, "error": str(exc)}
                )
                continue
            for discovered_tool in discovered:
                name = str(getattr(discovered_tool, "name", "") or "")
                if not name:
                    continue
                input_schema = getattr(discovered_tool, "input_schema", {})
                if not isinstance(input_schema, dict):
                    input_schema = {}
                server_id = connector_id_str
                server_name = str(
                    getattr(discovered_tool, "server_name", server_id) or server_id
                )
                tools.append(
                    ToolRef(
                        server_id=server_id,
                        server_name=server_name,
                        name=name,
                        description=str(getattr(discovered_tool, "description", "") or ""),
                        input_schema=input_schema,
                    )
                )

        connector_metadata = dict(agent)
        if connector_errors:
            connector_metadata["connector_errors"] = connector_errors
        return ToolContext(connectors=[connector_metadata], tools=tools)

    def _tenant_ctx_for_event_store(
        self, record: GoalRecord, tenant_ctx: TenantContext | None
    ) -> TenantContext:
        if tenant_ctx is not None:
            return tenant_ctx
        return TenantContext(
            tenant_id=record.tenant_id,
            plan=PlanTier.FREE,
            api_key_id="event-store-replay",
        )

    async def _persist_event(
        self,
        goal_id: str,
        event: dict[str, Any],
        record: GoalRecord,
        tenant_ctx: TenantContext | None,
    ) -> None:
        if self._event_store is None:
            return
        try:
            await self._event_store.append_event(
                goal_id,
                event,
                tenant_ctx=self._tenant_ctx_for_event_store(record, tenant_ctx),
            )
        except Exception as exc:
            _svc_logger.warning("DB persist goal event failed: %s", exc)

    async def _list_persisted_events(
        self, goal_id: str, tenant_ctx: TenantContext
    ) -> list[dict[str, Any]]:
        if self._event_store is None:
            return []
        try:
            events = await self._event_store.list_events(goal_id, tenant_ctx=tenant_ctx)
            return cast("list[dict[str, Any]]", events)
        except Exception as exc:
            _svc_logger.warning("DB list goal events failed: %s", exc)
            return []

    @staticmethod
    def _event_key(event: dict[str, Any]) -> str:
        try:
            return json.dumps(event, sort_keys=True, default=str)
        except TypeError:
            return repr(event)

    @classmethod
    def _merge_events_without_duplicates(
        cls, first: list[dict[str, Any]], second: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for event in [*first, *second]:
            key = cls._event_key(event)
            if key in seen:
                continue
            seen.add(key)
            merged.append(event)
        return merged

    async def _events_for_replay(
        self, goal_id: str, record: GoalRecord, tenant_ctx: TenantContext
    ) -> list[dict[str, Any]]:
        persisted_events = await self._list_persisted_events(goal_id, tenant_ctx)
        if not persisted_events:
            return list(record.events)
        if not record.events:
            return persisted_events
        return self._merge_events_without_duplicates(persisted_events, list(record.events))

    @staticmethod
    def _status_from_events(events: list[dict[str, Any]]) -> GoalStatus | None:
        for event in reversed(events):
            etype = event.get("type")
            if etype in {"goal_complete", "worker_complete"}:
                return GoalStatus.COMPLETE
            if etype in {"goal_failed", "worker_failed"}:
                return GoalStatus.FAILED
            if etype == "goal_cancelled":
                return GoalStatus.CANCELLED
        return None

    def _should_refresh_goal_from_db(self, record: GoalRecord) -> bool:
        if self._db is None:
            return False
        if self._task_queue is not None:
            return True
        return record.task is None and record.status not in _TERMINAL_STATUSES

    async def _refresh_goal_from_db_if_needed(
        self, record: GoalRecord, tenant_ctx: TenantContext
    ) -> GoalRecord:
        if not self._should_refresh_goal_from_db(record):
            return record
        persisted = await self._db_get_goal_record(record.goal_id, tenant_ctx)
        if persisted is None:
            return record
        return persisted

    async def _event_count_for_response(
        self, goal_id: str, record: GoalRecord, tenant_ctx: TenantContext
    ) -> int:
        events = await self._events_for_replay(goal_id, record, tenant_ctx)
        return len(events)

    async def _dispatch_event(
        self,
        goal_id: str,
        event: dict[str, Any],
        tenant_ctx: TenantContext | None = None,
    ) -> None:
        """Append *event* to the record and fan out to all live subscribers."""
        record = self._goals.get(goal_id)
        if record is None:
            return
        sanitized_event = sanitize_event(event)
        record.events.append(sanitized_event)
        await self._persist_event(goal_id, sanitized_event, record, tenant_ctx)
        # Reflect terminal status in the record and record metrics.
        etype = sanitized_event.get("type")
        if etype == "goal_complete":
            record.status = GoalStatus.COMPLETE
            record.completed_at = datetime.now(UTC).isoformat()
            self._record_terminal_goal_metrics(record, "completed")
            # Persist status update to PostgreSQL in the background.
            if self._db is not None:
                self._track_db_task(
                    self._db_update_goal_status(
                        goal_id,
                        record.tenant_id,
                        record.status.value,
                        error_message="",
                        iterations=len(record.events),
                    )
                )
            # ── Eval scoring on completion (Task 3) ────────────────────────────
            _logger = _svc_logger
            try:
                eval_runner = getattr(self._app_state, "eval_runner", None)
                if eval_runner is not None:
                    tenant_ctx_for_record: TenantContext = (
                        tenant_ctx
                        if tenant_ctx is not None
                        else TenantContext(
                            tenant_id=record.tenant_id,
                            plan=PlanTier.FREE,
                            api_key_id="eval-scoring",
                        )
                    )
                    # Build a minimal AgentState from the recorded events.
                    steps: list[StepResult] = []
                    verification_success = False
                    for evt in record.events:
                        if evt.get("type") == "plan_ready":
                            for step_text in evt.get("steps", []):
                                steps.append(
                                    StepResult(
                                        description=str(step_text),
                                        status=StepStatus.COMPLETE,
                                    )
                                )
                        elif evt.get("type") == "verification_done":
                            verification_success = bool(evt.get("success", False))
                    agent_state = AgentState(
                        goal_id=goal_id,
                        goal=record.goal_text,
                        tenant_ctx=tenant_ctx_for_record,
                        status=GoalStatus.COMPLETE,
                        iterations=len(record.events),
                        steps=steps,
                        verification_success=verification_success,
                    )
                    scorecard = eval_runner.score(
                        state=agent_state, tenant_ctx=tenant_ctx_for_record
                    )
                    self._eval_scores[goal_id] = scorecard
                    # Trigger self-optimizer when score falls below threshold.
                    if scorecard.average_score() < 0.7:
                        self_optimizer = getattr(self._app_state, "self_optimizer", None)
                        if self_optimizer is not None:
                            try:
                                failed_events = [
                                    e
                                    for e in record.events
                                    if "fail" in e.get("type", "").lower()
                                    or "error" in e.get("type", "").lower()
                                ]
                                error_log = " | ".join(
                                    e.get("reason", str(e)) for e in failed_events
                                )
                                self_optimizer.analyze_and_suggest(
                                    goal=record.goal_text,
                                    scorecard=scorecard,
                                    error_log=error_log,
                                    tenant_ctx=tenant_ctx_for_record,
                                )
                            except Exception as opt_exc:
                                _logger.warning(
                                    "Self-optimizer failed for goal %s: %s", goal_id, opt_exc
                                )
            except Exception as exc:
                _svc_logger.warning(
                    "Eval scoring failed for goal %s: %s", goal_id, exc
                )
        elif etype == "goal_failed":
            record.status = GoalStatus.FAILED
            record.completed_at = datetime.now(UTC).isoformat()
            self._record_terminal_goal_metrics(record, "failed")
            if self._db is not None:
                self._track_db_task(
                    self._db_update_goal_status(
                        goal_id,
                        record.tenant_id,
                        record.status.value,
                        error_message=record.events[-1].get("reason", "") if record.events else "",
                        iterations=len(record.events),
                    )
                )
        elif etype == "goal_cancelled":
            record.status = GoalStatus.CANCELLED
            record.completed_at = datetime.now(UTC).isoformat()
            self._record_terminal_goal_metrics(record, "cancelled")
        # Decrement the per-tenant concurrent-goal counter for every terminal event.
        if etype in {"goal_complete", "goal_failed", "goal_cancelled"}:
            from app.tenancy.limits import decrement_concurrent_goals
            await decrement_concurrent_goals(
                tenant_id=record.tenant_id, redis=self._redis
            )
        # Publish terminal events to Redis pub/sub for cross-replica SSE delivery.
        if etype in {"goal_complete", "goal_failed"} and self._redis and tenant_ctx:
            try:
                await self._redis.publish(
                    f"platform_events:{tenant_ctx.tenant_id}",
                    json.dumps(sanitized_event),
                )
            except Exception:
                pass
        # Push event to every live subscriber queue
        for q in list(record.subscribers):
            await q.put(sanitized_event)
        # If the goal has reached a terminal state, send the end-of-stream sentinel
        if record.status in {GoalStatus.COMPLETE, GoalStatus.FAILED, GoalStatus.CANCELLED}:
            for q in list(record.subscribers):
                await q.put(_SENTINEL)

    def _record_terminal_goal_metrics(self, record: GoalRecord, status: str) -> None:
        if record.terminal_metrics_recorded:
            return
        record.terminal_metrics_recorded = True
        duration_seconds = _monotonic() - record.started_monotonic
        record_goal_duration(
            status=status,
            duration_seconds=duration_seconds,
            priority=record.priority,
        )
        # Track per-tenant durations so get_metrics can compute avg_latency_ms.
        self._goal_durations.setdefault(record.tenant_id, []).append(duration_seconds)

    async def _run_agent_loop_persistent(
        self,
        goal_id: str,
        goal_text: str,
        tenant_ctx: TenantContext,
        tool_context: Any = None,
        persistence_config: dict[str, Any] | None = None,
    ) -> None:
        """Run agent with persistence — keep retrying until goal achieved."""
        from app.agent.persistence import GoalPersistenceEngine, PersistenceConfig

        record = self._goals.get(goal_id)

        async def callback(event: dict[str, Any]) -> None:
            await self._dispatch_event(goal_id, event, tenant_ctx=tenant_ctx)

        cfg_data = persistence_config or {}
        config = PersistenceConfig(
            max_attempts=cfg_data.get("max_attempts", 10),
            iterations_per_attempt=cfg_data.get("iterations_per_attempt", 15),
            base_backoff_seconds=cfg_data.get("base_backoff_seconds", 30.0),
            max_backoff_seconds=cfg_data.get("max_backoff_seconds", 600.0),
            strategy_switch_after=cfg_data.get("strategy_switch_after", 2),
            escalate_after_failures=cfg_data.get("escalate_after_failures", 6),
            total_timeout_seconds=cfg_data.get("total_timeout_seconds", 0.0),
            decompose_on_failure=cfg_data.get("decompose_on_failure", True),
        )

        engine = GoalPersistenceEngine(config=config)

        def agent_factory() -> Any:
            loop = self._make_agent_loop_for_tenant(tenant_ctx, self._app_state)
            if tool_context is not None:
                # Seed initial_context into the agent's run via a wrapper
                _tc = tool_context

                class _WrappedAgent:
                    async def run(
                        self_inner: "_WrappedAgent",
                        goal: str,
                        tenant_ctx: TenantContext,
                        event_callback: Any = None,
                    ) -> Any:
                        initial_ctx: dict[str, Any] = {
                            "tool_prompt": _tc.to_prompt_block(),
                            "tool_context": _tc,
                        }
                        return await loop.run(
                            goal=goal,
                            tenant_ctx=tenant_ctx,
                            initial_context=initial_ctx,
                            event_callback=event_callback,
                        )

                return _WrappedAgent()
            return loop

        try:
            success, attempts = await engine.run(
                goal=goal_text,
                agent_factory=agent_factory,
                tenant_ctx=tenant_ctx,
                event_callback=callback,
            )
            if not success:
                # All attempts exhausted
                reason = (
                    f"Goal could not be achieved after {len(attempts)} attempts. "
                    f"Last strategy: {attempts[-1].strategy if attempts else 'none'}. "
                    f"Total cost: ${engine.total_cost_usd:.4f}"
                )
                await self._dispatch_event(
                    goal_id,
                    {"type": "goal_failed", "reason": reason, "attempts": len(attempts)},
                    tenant_ctx=tenant_ctx,
                )
        except asyncio.CancelledError:
            if record is not None and record.status != GoalStatus.CANCELLED:
                record.status = GoalStatus.CANCELLED
                await self._dispatch_event(
                    goal_id, {"type": "goal_cancelled"}, tenant_ctx=tenant_ctx
                )
            raise
        except Exception as exc:
            await self._dispatch_event(
                goal_id, {"type": "goal_failed", "reason": str(exc)},
                tenant_ctx=tenant_ctx
            )

    async def _run_agent_loop(
        self,
        goal_id: str,
        goal_text: str,
        tenant_ctx: TenantContext,
        tool_context: ToolContext | None = None,
    ) -> None:
        """Background task: run the agent loop for a submitted goal."""
        with _tracer.start_as_current_span("goal.execute") as span:
            span.set_attribute("goal_id", goal_id)

            loop = self._make_agent_loop_for_tenant(tenant_ctx, self._app_state)
            record = self._goals.get(goal_id)
            # Store graph instance on record so HITL resume can re-invoke from checkpoint
            if record is not None:
                record._graph_instance = loop
            # Detect FakeProvider so get_goal() can surface a warning to callers
            if hasattr(loop, '_planner') and type(loop._planner).__name__ == 'FakeProvider':
                if record is not None:
                    record.execution_context['provider_warning'] = (
                        "No real LLM provider configured. Results are simulated."
                    )

            # Guarantee tool_context is always available: fall back to building
            # a basic context (RPA tools) when the caller didn't pass one.
            if tool_context is None:
                try:
                    tool_context = await self._build_tool_context(
                        agent_id=None, tenant_ctx=tenant_ctx
                    )
                except Exception as _tc_exc:
                    _svc_logger.warning("tool_context_build_failed", error=str(_tc_exc))

            initial_context: dict[str, Any] = {}
            if tool_context is not None:
                initial_context["tool_prompt"] = tool_context.to_prompt_block()
                initial_context["tool_context"] = tool_context

            async def callback(event: dict[str, Any]) -> None:
                await self._dispatch_event(goal_id, event, tenant_ctx=tenant_ctx)

            try:
                await loop.run(
                    goal=goal_text,
                    tenant_ctx=tenant_ctx,
                    initial_context=initial_context or None,
                    event_callback=callback,
                )
            except asyncio.CancelledError:
                if record is not None and record.status != GoalStatus.CANCELLED:
                    cancelled_event: dict[str, Any] = {"type": "goal_cancelled"}
                    record.status = GoalStatus.CANCELLED
                    await self._dispatch_event(goal_id, cancelled_event, tenant_ctx=tenant_ctx)
                raise
            except Exception as exc:
                if record is not None:
                    failed_event: dict[str, Any] = {"type": "goal_failed", "reason": str(exc)}
                    await self._dispatch_event(goal_id, failed_event, tenant_ctx=tenant_ctx)

    async def _run_workflow(
        self,
        goal_id: str,
        goal_text: str,
        tenant_ctx: TenantContext,
        tool_context: ToolContext | None = None,
    ) -> None:
        """Background task: run the minimal multi-agent workflow path."""
        record = self._goals.get(goal_id)
        if record is not None:
            record.status = GoalStatus.EXECUTING

        async def callback(event: dict[str, Any]) -> None:
            await self._dispatch_event(goal_id, event, tenant_ctx=tenant_ctx)

        try:
            await self._dispatch_event(
                goal_id, {"type": "goal_started", "goal": goal_text}, tenant_ctx=tenant_ctx
            )
            plan = build_static_workflow(goal_text)
            executor = WorkflowExecutor(mcp_client=self._get_mcp_client())
            await executor.run(
                plan=plan,
                goal=goal_text,
                tenant_ctx=tenant_ctx,
                tool_context=tool_context,
                event_callback=callback,
            )
            await self._dispatch_event(goal_id, {"type": "goal_complete"}, tenant_ctx=tenant_ctx)
        except asyncio.CancelledError:
            if record is not None and record.status != GoalStatus.CANCELLED:
                record.status = GoalStatus.CANCELLED
                await self._dispatch_event(
                    goal_id, {"type": "goal_cancelled"}, tenant_ctx=tenant_ctx
                )
            raise
        except Exception as exc:
            await self._dispatch_event(
                goal_id, {"type": "goal_failed", "reason": str(exc)}, tenant_ctx=tenant_ctx
            )

    # ── public API ────────────────────────────────────────────────────────────

    async def submit_goal(
        self,
        goal: str,
        priority: str,
        dry_run: bool,
        tenant_ctx: TenantContext,
        agent_id: str | None = None,
        workflow_mode: str = "single_agent",
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a goal record and (unless *dry_run*) launch it as a background task."""
        with _tracer.start_as_current_span("goal.submit") as span:
            span.set_attribute("tenant_id", tenant_ctx.tenant_id)
            span.set_attribute("goal", goal[:100])
            self._validate_agent_id(agent_id, tenant_ctx)

            # Enforce daily goal limit per plan tier (Redis-backed for multi-process safety)
            await self._check_daily_goal_limit_redis(tenant_ctx)

            # Check and atomically increment the concurrent-goal counter.
            # Raises PlanLimitExceededError (HTTP 429) when the tenant is at limit.
            from app.tenancy.limits import check_and_increment_concurrent_goals
            await check_and_increment_concurrent_goals(
                tenant_ctx=tenant_ctx,
                redis=getattr(self, "_redis", None),
            )

            goal_id = uuid.uuid4().hex

            # Auto-route to best agent when agent_id not specified
            if agent_id is None and self._app_state is not None:
                agent_store = self._get_agent_store()
                if agent_store is not None:
                    from app.agent.router import AgentRouter
                    router = AgentRouter(agent_store=agent_store)
                    try:
                        decision = await router.route(goal, tenant_ctx)
                        if decision.agent_id and decision.confidence >= 0.3:
                            agent_id = decision.agent_id
                            _svc_logger.info(
                                "auto_routed_goal",
                                goal_id=goal_id,
                                agent_id=agent_id,
                                confidence=decision.confidence,
                            )
                    except Exception as exc:
                        _svc_logger.warning("agent_router_failed", error=str(exc))

            record = GoalRecord(
                goal_id=goal_id,
                goal_text=goal,
                status=GoalStatus.PLANNING,
                tenant_id=tenant_ctx.tenant_id,
                priority=priority,
                dry_run=dry_run,
                created_at=datetime.now(UTC).isoformat(),
                agent_id=agent_id,
                workflow_mode=workflow_mode,
                execution_context=execution_context or {},
            )
            self._goals[goal_id] = record

            # Time-based eviction: evict stale terminal goals to prevent OOM.
            now = time.monotonic()
            if now - self._last_eviction_time > _EVICTION_INTERVAL_SECONDS:
                self._last_eviction_time = now
                asyncio.create_task(self._evict_async())

            # Fix 6: record that a new goal has been started.
            record_goal_started(tenant_id=tenant_ctx.tenant_id, priority=priority)

            # Persist to PostgreSQL in the background when a DB factory is wired.
            if self._db is not None and self._task_queue is not None and not dry_run:
                await self._db_persist_goal(
                    goal_id=goal_id,
                    tenant_id=tenant_ctx.tenant_id,
                    goal_text=goal,
                    status=GoalStatus.PLANNING.value,
                    priority=priority,
                    dry_run=dry_run,
                    agent_id=agent_id,
                    workflow_mode=workflow_mode,
                    execution_context=record.execution_context,
                    raise_on_error=True,
                )
            elif self._db is not None:
                self._track_db_task(
                    self._db_persist_goal(
                        goal_id=goal_id,
                        tenant_id=tenant_ctx.tenant_id,
                        goal_text=goal,
                        status=GoalStatus.PLANNING.value,
                        priority=priority,
                        dry_run=dry_run,
                        agent_id=agent_id,
                        workflow_mode=workflow_mode,
                        execution_context=record.execution_context,
                    )
                )

            if not dry_run:
                if self._task_queue is not None:
                    self._task_queue.enqueue_goal(
                        goal_id=goal_id,
                        tenant_id=tenant_ctx.tenant_id,
                        goal_text=goal,
                        priority=priority,
                        dry_run=dry_run,
                        agent_id=agent_id,
                        workflow_mode=workflow_mode,
                        goal_template="",
                    )
                else:
                    tool_context = await self._build_tool_context(
                        agent_id=agent_id, tenant_ctx=tenant_ctx
                    )
                    if workflow_mode == "multi_agent":
                        workflow_task = asyncio.create_task(
                            self._run_workflow(
                                goal_id, goal, tenant_ctx, tool_context=tool_context
                            ),
                            name=f"goal-workflow-{goal_id}",
                        )
                        record.task = workflow_task
                        return {
                            "goal_id": goal_id,
                            "status": record.status.value,
                            "goal": goal,
                            "priority": priority,
                            "dry_run": dry_run,
                            "agent_id": record.agent_id,
                            "workflow_mode": record.workflow_mode,
                            "created_at": record.created_at,
                        }
                    persistence_mode = (execution_context or {}).get(
                        "persistence_mode", False
                    )
                    persistence_cfg = (execution_context or {}).get(
                        "persistence_config", {}
                    )
                    if persistence_mode:
                        agent_task: asyncio.Task[None] = asyncio.create_task(
                            self._run_agent_loop_persistent(
                                goal_id, goal, tenant_ctx,
                                tool_context=tool_context,
                                persistence_config=persistence_cfg,
                            ),
                            name=f"goal-persistent-{goal_id}",
                        )
                    else:
                        agent_task = asyncio.create_task(
                            self._run_agent_loop(
                                goal_id, goal, tenant_ctx, tool_context=tool_context
                            ),
                            name=f"goal-{goal_id}",
                        )
                    record.task = agent_task
            else:
                # Dry-run: validate intent only, but still emit visible lifecycle
                # events so completed dry-runs are explainable in the UI.
                await self._dispatch_event(
                    goal_id, {"type": "goal_started", "goal": goal}, tenant_ctx=tenant_ctx
                )
                await self._dispatch_event(
                    goal_id,
                    {
                        "type": "dry_run_preview",
                        "message": "Dry run completed without executing tools or writing changes.",
                        "would_execute": False,
                    },
                    tenant_ctx=tenant_ctx,
                )
                await self._dispatch_event(
                    goal_id, {"type": "goal_complete"}, tenant_ctx=tenant_ctx
                )
                # Don't count dry-runs against the daily Redis limit — decrement it back
                _redis_for_dry = getattr(self, "_redis", None)
                if _redis_for_dry is not None:
                    try:
                        _today = datetime.now(UTC).strftime("%Y-%m-%d")
                        _dry_key = f"daily_goals:{tenant_ctx.tenant_id}:{_today}"
                        await _redis_for_dry.decr(_dry_key)
                    except Exception:
                        pass

        return {
            "goal_id": goal_id,
            "status": record.status.value,
            "goal": goal,
            "priority": priority,
            "dry_run": dry_run,
            "agent_id": record.agent_id,
            "workflow_mode": record.workflow_mode,
            "created_at": record.created_at,
        }

    async def get_goal(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
        """Return goal metadata and current status."""
        record = self._goals.get(goal_id)
        if record is None or record.tenant_id != tenant_ctx.tenant_id:
            record = await self._db_get_goal_record(goal_id, tenant_ctx)
        else:
            record = await self._refresh_goal_from_db_if_needed(record, tenant_ctx)
        if record is None:
            raise NotFoundError(f"Goal not found: {goal_id}")
        event_count = await self._event_count_for_response(goal_id, record, tenant_ctx)
        return {
            "goal_id": record.goal_id,
            "status": record.status.value,
            "goal": record.goal_text,
            "priority": record.priority,
            "dry_run": record.dry_run,
            "agent_id": record.agent_id,
            "workflow_mode": record.workflow_mode,
            "created_at": record.created_at,
            "event_count": event_count,
            "provider_warning": record.execution_context.get("provider_warning"),
        }

    async def list_goals(self, tenant_ctx: TenantContext) -> dict[str, list[dict[str, Any]]]:
        """Return all goals visible to the tenant, newest first."""
        tenant_records = []
        for record in self._goals.values():
            if record.tenant_id != tenant_ctx.tenant_id:
                continue
            tenant_records.append(await self._refresh_goal_from_db_if_needed(record, tenant_ctx))
        tenant_records.sort(key=lambda record: record.created_at, reverse=True)

        # Single batch query for DB event counts — replaces the previous per-goal
        # call to _event_count_for_response() which caused N+1 DB round-trips.
        goal_ids = [r.goal_id for r in tenant_records]
        batch_counts = await self._batch_event_counts(goal_ids, tenant_ctx.tenant_id)

        responses: list[dict[str, Any]] = []
        for record in tenant_records:
            # Use the larger of DB count and in-memory count to stay correct
            # when some events have not yet been flushed to the DB.
            event_count = max(batch_counts.get(record.goal_id, 0), len(record.events))
            responses.append(
                {
                    "id": record.goal_id,
                    "goal_id": record.goal_id,
                    "status": record.status.value,
                    "goal": record.goal_text,
                    "priority": record.priority,
                    "dry_run": record.dry_run,
                    "agent_id": record.agent_id,
                    "workflow_mode": record.workflow_mode,
                    "created_at": record.created_at,
                    "event_count": event_count,
                }
            )
        return {
            "goals": responses
        }

    async def get_metrics(self, tenant_ctx: TenantContext) -> dict[str, Any]:
        """Return aggregated metrics for the tenant's goals — reads from DB when available."""
        # Try DB-backed metrics first (works correctly in Celery/multi-process mode)
        if self._db is not None:
            try:
                from sqlalchemy import text
                async with self._db() as session:
                    row = (await session.execute(text("""
                        SELECT
                          COUNT(*) FILTER (WHERE status IN ('complete','completed')) AS completed,
                          COUNT(*) FILTER (WHERE status IN ('failed','error')) AS failed,
                          COUNT(*) FILTER (WHERE status IN ('planning','executing','waiting_human')) AS active,
                          COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled,
                          COUNT(*) FILTER (WHERE status IN ('complete','completed') AND created_at::date = CURRENT_DATE) AS completed_today,
                          AVG(EXTRACT(EPOCH FROM (completed_at - created_at))*1000) FILTER (WHERE status IN ('complete','completed') AND completed_at IS NOT NULL) AS avg_latency_ms,
                          COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE) AS submitted_today
                        FROM goals WHERE tenant_id = :tid
                    """), {"tid": tenant_ctx.tenant_id})).fetchone()

                    if row:
                        completed = row[0] or 0
                        failed = row[1] or 0
                        active = row[2] or 0
                        cancelled = row[3] or 0
                        terminal = completed + failed + cancelled
                        cost_today_usd = 0.0
                        cost_ctrl = (
                            getattr(self._app_state, "redis_cost_controller", None)
                            or getattr(self._app_state, "cost_controller", None)
                        ) if self._app_state else None
                        if cost_ctrl is not None:
                            try:
                                _cost_val = cost_ctrl.get_tenant_cost_today(tenant_ctx)
                                if hasattr(_cost_val, "__await__"):
                                    cost_today_usd = await _cost_val
                                else:
                                    cost_today_usd = float(_cost_val or 0)
                            except Exception:
                                cost_today_usd = 0.0
                        return {
                            "active_goals": active,
                            "completed_goals": completed,
                            "failed_goals": failed,
                            "cancelled_goals": cancelled,
                            "completed_today": row[4] or 0,
                            "submitted_today": row[6] or 0,
                            "success_rate": round(completed / terminal, 3) if terminal > 0 else 0.0,
                            "avg_latency_ms": round(float(row[5] or 0), 1),
                            "cost_today_usd": cost_today_usd,
                            # Legacy fields for backward compat
                            "total_goals": completed + failed + active + cancelled,
                            "goals_today": row[6] or 0,
                        }
            except Exception as exc:
                _svc_logger.warning("get_metrics_db_failed", error=str(exc))

        # Fallback: in-memory calculation
        today = datetime.now(UTC).date().isoformat()
        active_goals = 0
        total_goals = 0
        completed_goals = 0
        failed_goals = 0
        cancelled_goals = 0
        goals_today = 0

        for record in self._goals.values():
            if record.tenant_id != tenant_ctx.tenant_id:
                continue
            total_goals += 1
            if record.status not in _TERMINAL_STATUSES:
                active_goals += 1
            if record.status == GoalStatus.COMPLETE:
                completed_goals += 1
            elif record.status == GoalStatus.FAILED:
                failed_goals += 1
            elif record.status == GoalStatus.CANCELLED:
                cancelled_goals += 1
            if record.created_at.startswith(today):
                goals_today += 1

        # Use only terminal goals as denominator so that in-progress goals
        # do not dilute the success rate.
        terminal_goals = completed_goals + failed_goals + cancelled_goals
        success_rate = completed_goals / terminal_goals if terminal_goals > 0 else 0.0

        durations = self._goal_durations.get(tenant_ctx.tenant_id, [])
        avg_latency_ms = (sum(durations) / len(durations) * 1000.0) if durations else 0.0

        cost_today_usd = 0.0
        cost_controller = getattr(self._app_state, "cost_controller", None)
        if cost_controller is not None:
            try:
                cost_today_usd = cost_controller.get_tenant_cost_today(tenant_ctx)
            except Exception:
                cost_today_usd = 0.0

        return {
            "active_goals": active_goals,
            "total_goals": total_goals,
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency_ms,
            "cost_today_usd": cost_today_usd,
            "goals_today": goals_today,
        }

    async def get_eval(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
        """Return the eval scorecard for *goal_id*, or a not-evaluated response."""
        self._get_record(goal_id, tenant_ctx)  # raises if not found / wrong tenant
        scorecard = self._eval_scores.get(goal_id)
        if scorecard is None:
            return {
                "goal_id": goal_id,
                "status": "not_evaluated",
                "scores": {},
                "average_score": None,
                "passed": None,
            }
        return {
            "goal_id": scorecard.goal_id,
            "status": "evaluated",
            "scores": scorecard.scores,
            "average_score": scorecard.average_score(),
            "passed": scorecard.passed(),
            "iterations": scorecard.iterations,
        }

    async def cancel_goal(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
        """Cancel a running goal.  Idempotent if the goal is already terminal."""
        record = self._get_record(goal_id, tenant_ctx)
        if record.task is not None and not record.task.done():
            record.task.cancel()
        record.status = GoalStatus.CANCELLED
        cancelled_event: dict[str, Any] = {"type": "goal_cancelled"}
        await self._dispatch_event(goal_id, cancelled_event, tenant_ctx=tenant_ctx)
        return {"goal_id": goal_id, "status": GoalStatus.CANCELLED.value}

    async def pause_goal(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
        """Pause a running goal. The agent loop will honour the pause event."""
        record = self._get_record(goal_id, tenant_ctx)
        if record.status not in {GoalStatus.EXECUTING, GoalStatus.PLANNING}:
            raise ValueError(
                f"Goal {goal_id} is not running (status: {record.status.value})"
            )
        _GOAL_PAUSE_EVENTS[goal_id] = asyncio.Event()
        record.status = GoalStatus.WAITING_HUMAN
        await self._dispatch_event(goal_id, {"type": "goal_paused"}, tenant_ctx=tenant_ctx)

        # Publish pause signal to Redis so Celery workers can observe it
        redis = getattr(self, "_redis", None)
        if redis is not None:
            try:
                await redis.publish(f"goal_pause:{goal_id}", "pause")
                await redis.set(f"goal_paused:{goal_id}", "1", ex=3600)  # flag for polling
            except Exception as exc:
                _svc_logger.warning("pause_publish_failed", goal_id=goal_id, error=str(exc))

        return {"goal_id": goal_id, "status": "paused"}

    async def resume_goal(
        self,
        goal_id: str,
        tenant_ctx: TenantContext,
        *,
        approved: bool = True,
        feedback: str = "",
    ) -> dict[str, Any]:
        """Resume a paused goal.

        When *approved* is False the goal is immediately failed with the
        rejection feedback.  When True the goal is resumed — either by
        re-invoking the stored AgentGraph from its LangGraph checkpoint, or by
        firing the legacy asyncio pause-event (backward-compatible fallback).
        """
        record = self._get_record(goal_id, tenant_ctx)
        if record.status in _TERMINAL_STATUSES:
            raise ValueError(
                f"Goal {goal_id} is already terminal (status: {record.status.value})"
            )

        if not approved:
            record.status = GoalStatus.FAILED
            record.execution_context["hitl_rejected"] = True
            record.execution_context["hitl_feedback"] = feedback
            await self._dispatch_event(
                goal_id,
                {"type": "goal_failed", "reason": f"HITL rejected: {feedback}"},
                tenant_ctx=tenant_ctx,
            )
            return {"goal_id": goal_id, "status": "rejected"}

        # Store approval decision so the graph can read it on resume
        record.execution_context["hitl_approved"] = True
        record.execution_context["hitl_feedback"] = feedback

        # Attempt LangGraph checkpoint re-invocation when a graph instance is stored
        graph = getattr(record, "_graph_instance", None)
        if graph is not None:
            config = {"configurable": {"thread_id": goal_id}}
            try:
                import asyncio as _asyncio

                async def _resume_graph() -> None:
                    resume_input = {
                        "hitl_decision": "approved",
                        "hitl_feedback": feedback,
                    }
                    try:
                        async for _ in graph._graph.astream(resume_input, config=config):
                            pass
                    except Exception as _inner_exc:
                        _svc_logger.warning(
                            "hitl_checkpoint_resume_task_failed", error=str(_inner_exc)
                        )
                        # Fallback: fire asyncio pause event
                        evt = _GOAL_PAUSE_EVENTS.pop(goal_id, None)
                        if evt is not None:
                            evt.set()

                _asyncio.create_task(_resume_graph())
                record.status = GoalStatus.EXECUTING
                await self._dispatch_event(
                    goal_id, {"type": "goal_resumed", "method": "checkpoint"}, tenant_ctx=tenant_ctx
                )
                return {"goal_id": goal_id, "status": "resumed"}
            except Exception as exc:
                _svc_logger.warning("hitl_checkpoint_resume_failed", error=str(exc))

        # Fallback: fire any waiting asyncio pause-event (legacy path)
        record.status = GoalStatus.EXECUTING
        record.events.append(
            {"type": "hitl_approved", "feedback": feedback, "ts": datetime.now(UTC).isoformat()}
        )
        evt = _GOAL_PAUSE_EVENTS.pop(goal_id, None)
        if evt is not None:
            evt.set()
        await self._dispatch_event(goal_id, {"type": "goal_resumed"}, tenant_ctx=tenant_ctx)
        return {"goal_id": goal_id, "status": "resumed"}

    async def get_events(self, goal_id: str, tenant_ctx: TenantContext) -> list[dict[str, Any]]:
        """Return a snapshot of all SSE events emitted so far for *goal_id*."""
        record = self._get_record(goal_id, tenant_ctx)
        if not record.events:
            persisted_events = await self._list_persisted_events(goal_id, tenant_ctx)
            if persisted_events:
                return persisted_events
        return list(record.events)

    async def subscribe_events(
        self,
        goal_id: str,
        tenant_ctx: TenantContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Async generator that yields SSE events for *goal_id* in real time."""
        record = self._get_record(goal_id, tenant_ctx)

        # Replay persisted events and in-memory events without duplicating events
        # already recovered from the durable stream.
        replay_events = await self._events_for_replay(goal_id, record, tenant_ctx)
        for event in replay_events:
            yield event

        replay_status = self._status_from_events(replay_events)
        if replay_status is not None:
            record.status = replay_status

        # If goal is already terminal, nothing more to await
        if record.status in _TERMINAL_STATUSES:
            return

        record = await self._refresh_goal_from_db_if_needed(record, tenant_ctx)
        if record.status in _TERMINAL_STATUSES:
            return

        # Register a subscriber queue for future events
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        record.subscribers.append(queue)
        try:
            while True:
                item = await queue.get()
                if item is None:  # end-of-stream
                    break
                yield item
        finally:
            with suppress(ValueError):
                record.subscribers.remove(queue)

    # ── governance delegations ────────────────────────────────────────────────

    async def get_audit_entries(
        self,
        goal_id: str,
        tenant_ctx: TenantContext,
    ) -> list[dict[str, Any]]:
        """Return audit log entries for *goal_id* (tenant-validated)."""
        self._get_record(goal_id, tenant_ctx)  # raises if not found / wrong tenant
        # Prefer DB query (works across all processes / Celery workers)
        if hasattr(self._audit_log, "query_db"):
            try:
                db_entries = await self._audit_log.query_db(
                    tenant_ctx=tenant_ctx,
                    goal_id=goal_id,
                    limit=500,
                )
                return [
                    {
                        "event_id": e.event_id,
                        "goal_id": e.goal_id,
                        "tool_name": e.tool_name,
                        "outcome": e.outcome,
                        "created_at": e.created_at,
                    }
                    for e in db_entries
                ]
            except Exception:
                pass
        # Fallback to in-memory
        entries = self._audit_log.query(tenant_ctx=tenant_ctx, goal_id=goal_id)
        return [
            {
                "event_id": e.event_id,
                "goal_id": e.goal_id,
                "tool_name": e.tool_name,
                "action_level": e.action_level.value,
                "outcome": e.outcome,
                "step_id": e.step_id,
                "approver": e.approver,
                "note": e.note,
            }
            for e in entries
        ]

    async def handle_approval(
        self,
        goal_id: str,
        request_id: str,
        action: str,
        approver: str,
        note: str,
        tenant_ctx: TenantContext,
    ) -> dict[str, Any]:
        """Approve or reject a pending HITL request for *goal_id*."""
        self._get_record(goal_id, tenant_ctx)  # raises if not found / wrong tenant
        if action == "approve":
            ok = self._hitl.approve(request_id, approver=approver, note=note, tenant_ctx=tenant_ctx)
        elif action == "reject":
            ok = self._hitl.reject(request_id, approver=approver, note=note, tenant_ctx=tenant_ctx)
        else:
            ok = False
        return {"request_id": request_id, "action": action, "accepted": ok}

    # ── DB persistence helpers ────────────────────────────────────────────────

    async def _db_persist_goal(
        self,
        goal_id: str,
        tenant_id: str,
        goal_text: str,
        status: str,
        priority: str,
        dry_run: bool,
        agent_id: str | None = None,
        workflow_mode: str = "single_agent",
        execution_context: dict[str, Any] | None = None,
        raise_on_error: bool = False,
    ) -> None:
        """Persist goal record to PostgreSQL."""
        if self._db is None:
            return
        try:
            from app.db.models.goal import Goal
            from app.db.rls import sqlalchemy_rls_context

            async with self._db() as session, session.begin(), sqlalchemy_rls_context(
                session, tenant_id
            ):
                g = Goal(
                    id=goal_id,
                    tenant_id=tenant_id,
                    goal_text=goal_text,
                    status=status,
                    priority=priority,
                    dry_run=dry_run,
                    agent_id=agent_id,
                    workflow_mode=workflow_mode,
                    execution_context=execution_context or {},
                )
                session.add(g)
        except Exception as exc:
            

            _svc_logger.warning("DB persist goal failed: %s", exc)
            if raise_on_error:
                raise

    async def _db_ensure_goal_row(
        self,
        *,
        goal_id: str,
        tenant_id: str,
        goal_text: str,
        status: str,
        priority: str,
        dry_run: bool,
        agent_id: str | None = None,
        workflow_mode: str = "single_agent",
        execution_context: dict[str, Any] | None = None,
    ) -> None:
        """Create the durable goal row before worker events reference it."""
        if self._db is None:
            return
        tenant_ctx = TenantContext(
            tenant_id=tenant_id,
            plan=PlanTier.FREE,
            api_key_id="goal-row-ensure",
        )
        existing = await self._db_get_goal_record(goal_id, tenant_ctx)
        if existing is not None:
            return
        await self._db_persist_goal(
            goal_id=goal_id,
            tenant_id=tenant_id,
            goal_text=goal_text,
            status=status,
            priority=priority,
            dry_run=dry_run,
            agent_id=agent_id,
            workflow_mode=workflow_mode,
            execution_context=execution_context or {},
            raise_on_error=True,
        )

    async def _db_get_goal_record(
        self, goal_id: str, tenant_ctx: TenantContext
    ) -> GoalRecord | None:
        """Load one goal from PostgreSQL when this process has no memory record."""
        if self._db is None:
            return None
        try:
            from sqlalchemy import select

            from app.db.models.goal import Goal
            from app.db.rls import sqlalchemy_rls_context

            async with self._db() as session, session.begin(), sqlalchemy_rls_context(
                session, tenant_ctx.tenant_id
            ):
                result = await session.execute(
                    select(Goal).where(
                        Goal.id == goal_id,
                        Goal.tenant_id == tenant_ctx.tenant_id,
                    )
                )
                row = result.scalar_one_or_none()
            if row is None:
                return None
            try:
                status = GoalStatus(row.status)
            except ValueError:
                status = GoalStatus.PLANNING
            record = GoalRecord(
                goal_id=row.id,
                goal_text=row.goal_text,
                status=status,
                tenant_id=row.tenant_id,
                priority=row.priority,
                dry_run=row.dry_run,
                created_at=row.created_at.isoformat() if row.created_at else "",
                agent_id=row.agent_id,
                workflow_mode=row.workflow_mode,
                execution_context=row.execution_context or {},
            )
            self._goals[row.id] = record
            return record
        except Exception as exc:
            

            _svc_logger.warning("DB get goal failed: %s", exc)
            return None

    async def _db_update_goal_status(
        self,
        goal_id: str,
        tenant_id: str,
        status: str,
        error_message: str = "",
        iterations: int = 0,
    ) -> None:
        """Update goal status in PostgreSQL."""
        if self._db is None:
            return
        try:
            from datetime import datetime

            from sqlalchemy import update

            from app.db.models.goal import Goal
            from app.db.rls import sqlalchemy_rls_context

            values: dict[str, Any] = {"status": status, "iterations": iterations}
            if error_message:
                values["error_message"] = error_message
            if status == "complete":
                values["completed_at"] = datetime.now(UTC)
            async with self._db() as session, session.begin(), sqlalchemy_rls_context(
                session, tenant_id
            ):
                await session.execute(
                    update(Goal)
                    .where(Goal.id == goal_id, Goal.tenant_id == tenant_id)
                    .values(**values)
                )
        except Exception as exc:
            

            _svc_logger.warning("DB update goal status failed: %s", exc)

    async def _db_persist_step(
        self,
        goal_id: str,
        tenant_id: str,
        step_index: int,
        description: str,
        status: str,
        output: str,
    ) -> None:
        """Persist goal step to PostgreSQL."""
        if self._db is None:
            return
        try:
            import uuid as _uuid

            from app.db.models.goal import GoalStep
            from app.db.rls import sqlalchemy_rls_context

            async with self._db() as session, session.begin(), sqlalchemy_rls_context(
                session, tenant_id
            ):
                s = GoalStep(
                    id=_uuid.uuid4().hex,
                    goal_id=goal_id,
                    tenant_id=tenant_id,
                    step_index=step_index,
                    description=description,
                    status=status,
                    output=output,
                )
                session.add(s)
        except Exception as exc:
            

            _svc_logger.warning("DB persist step failed: %s", exc)

    async def sync_from_db(self) -> int:
        """Load goals from PostgreSQL into memory on startup.

        Returns number of goals loaded.
        """
        if self._db is None:
            return 0
        try:
            from datetime import datetime, timedelta

            from sqlalchemy import select

            from app.db.models.goal import Goal
            from app.db.models.tenant import Tenant
            from app.db.rls import sqlalchemy_rls_context

            loaded = 0
            async with self._db() as session:
                tenant_result = await session.execute(
                    select(Tenant).where(Tenant.is_active == True)  # noqa: E712
                )
                tenants = tenant_result.scalars().all()

                # Load recent goals (last 24h). Recovery/requeue is intentionally
                # not implemented in this phase; loaded records only make metadata
                # and persisted event replay addressable after restart.
                cutoff = datetime.now(UTC) - timedelta(hours=24)
                for tenant in tenants:
                    tenant_id = str(tenant.id)
                    async with sqlalchemy_rls_context(session, tenant_id):
                        result = await session.execute(
                            select(Goal).where(
                                Goal.tenant_id == tenant_id,
                                Goal.created_at >= cutoff,
                            )
                        )
                    goals = result.scalars().all()
                    for g in goals:
                        if g.id not in self._goals:
                            try:
                                _g_status = GoalStatus(g.status)
                            except ValueError:
                                _g_status = GoalStatus.PLANNING
                            record = GoalRecord(
                                goal_id=g.id,
                                goal_text=g.goal_text,
                                status=_g_status,
                                tenant_id=g.tenant_id,
                                priority=g.priority,
                                dry_run=g.dry_run,
                                created_at=g.created_at.isoformat() if g.created_at else "",
                                agent_id=g.agent_id,
                                workflow_mode=g.workflow_mode,
                                execution_context=g.execution_context or {},
                            )
                            self._goals[g.id] = record
                            loaded += 1
            

            _svc_logger.info("Synced %d recent goals from DB", loaded)
            # Re-enqueue any goal that was interrupted mid-execution by a restart.
            recovered = await self._recover_interrupted_goals()
            if recovered:
                _svc_logger.info("Recovered %d interrupted goals after restart", recovered)
            return loaded
        except Exception as exc:
            

            _svc_logger.warning("DB sync goals failed: %s", exc)
            return 0
