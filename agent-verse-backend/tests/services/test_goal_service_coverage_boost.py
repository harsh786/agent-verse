"""Additional coverage for app/services/goal_service.py.

Targets specific large uncovered branches identified via
``--cov=app.services.goal_service --cov-report=term-missing``:

  - ``_subscribe_celery_goal_events`` (Celery -> SSE bridge): stub-record
    creation, event fan-out, dead-subscriber pruning, terminal-event status
    updates, and the outer redis-connect error path.
  - ``_run_agent_loop_persistent``: success, exhausted-attempts, cancellation,
    and generic-exception paths, including the ``record.runtime_profile``
    branch of the persistence-config computation.
  - ``_run_agent_loop_isolated``: scheduler success/failure, plus
    ``RunnerUnavailableError`` and generic-exception handling.
  - ``submit_goal`` auto-routing: pre-wired ``agent_router`` decision path,
    multi-agent spawn fan-out, and the best-scored-fallback path when no
    router is wired.
  - DB-backed ``list_goals``, ``get_metrics``, and ``_db_persist_step``.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.state import GoalStatus
from app.governance.audit import AuditLog
from app.governance.hitl import HITLGateway
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tenant_id: str = "cb-t1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k1")


def _svc() -> GoalService:
    return GoalService(audit_log=AuditLog(), hitl=HITLGateway())


def _inject_goal(
    svc: GoalService,
    goal_id: str = "g1",
    tenant_id: str = "cb-t1",
    status: str = "executing",
    goal_text: str = "Do something",
    runtime_profile: Any = None,
) -> GoalRecord:
    record = GoalRecord(
        goal_id=goal_id,
        goal_text=goal_text,
        status=GoalStatus(status),
        tenant_id=tenant_id,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
        runtime_profile=runtime_profile,
    )
    svc._goals[goal_id] = record
    return record


def _make_async_cm(return_value: Any = None):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=return_value)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_mock_db():
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(
            fetchall=MagicMock(return_value=[]),
            fetchone=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            scalar_one_or_none=MagicMock(return_value=None),
            rowcount=0,
        )
    )
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.begin = MagicMock(return_value=_make_async_cm(None))

    db = MagicMock()
    db.return_value = _make_async_cm(session)
    return db, session


# ── _subscribe_celery_goal_events (Celery -> SSE bridge) ──────────────────────


class TestSubscribeCeleryGoalEvents:
    async def test_redis_connect_failure_hits_outer_except_and_sleeps(self):
        svc = _svc()
        with (
            patch("redis.asyncio.from_url", side_effect=Exception("boom")),
            patch("asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError())),
        ):
            task = asyncio.create_task(svc._subscribe_celery_goal_events("redis://x"))
            with suppress(asyncio.CancelledError):
                await task

    async def test_creates_stub_record_for_unknown_goal(self):
        """No existing GoalRecord -> a stub is created from the bridged event."""
        svc = _svc()
        goal_id = "celery-goal-stub"

        async def _async_gen():
            yield {
                "type": "pmessage",
                "data": json.dumps(
                    {
                        "goal_id": goal_id,
                        "tenant_id": "cb-t1",
                        "type": "step_started",
                        "payload": {"i": 1},
                    }
                ),
            }
            raise asyncio.CancelledError()

        mock_pubsub = AsyncMock()
        mock_pubsub.psubscribe = AsyncMock()
        mock_pubsub.listen = _async_gen

        mock_redis_ctx = AsyncMock()
        mock_redis_ctx.__aenter__ = AsyncMock(return_value=mock_redis_ctx)
        mock_redis_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_redis_ctx.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("redis.asyncio.from_url", return_value=mock_redis_ctx):
            task = asyncio.create_task(svc._subscribe_celery_goal_events("redis://x"))
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2.0)

        record = svc._goals[goal_id]
        assert record.tenant_id == "cb-t1"
        assert record.status == GoalStatus.EXECUTING

    async def test_dispatches_to_subscribers_prunes_dead_and_marks_terminal(self):
        """Existing GoalRecord with subscribers -> event fanned to queues, a
        QueueFull subscriber for a non-heartbeat event is pruned, and a
        terminal event marks the record COMPLETE and sentinels subscribers."""
        svc = _svc()
        goal_id = "celery-goal-dispatch"
        record = _inject_goal(svc, goal_id, tenant_id="cb-t1", status="executing")

        good_queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        full_queue = MagicMock()
        full_queue.put_nowait = MagicMock(side_effect=asyncio.QueueFull())
        record.subscribers.extend([good_queue, full_queue])

        async def _async_gen():
            yield {
                "type": "pmessage",
                "data": json.dumps(
                    {
                        "goal_id": goal_id,
                        "tenant_id": "cb-t1",
                        "type": "step_started",
                        "payload": {"i": 1},
                    }
                ),
            }
            yield {
                "type": "pmessage",
                "data": json.dumps(
                    {
                        "goal_id": goal_id,
                        "tenant_id": "cb-t1",
                        "type": "goal_complete",
                        "payload": {},
                    }
                ),
            }
            raise asyncio.CancelledError()

        mock_pubsub = AsyncMock()
        mock_pubsub.psubscribe = AsyncMock()
        mock_pubsub.listen = _async_gen

        mock_redis_ctx = AsyncMock()
        mock_redis_ctx.__aenter__ = AsyncMock(return_value=mock_redis_ctx)
        mock_redis_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_redis_ctx.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("redis.asyncio.from_url", return_value=mock_redis_ctx):
            task = asyncio.create_task(svc._subscribe_celery_goal_events("redis://x"))
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2.0)

        assert record.status == GoalStatus.COMPLETE
        assert full_queue not in record.subscribers
        first = good_queue.get_nowait()
        assert first["type"] == "step_started"
        second = good_queue.get_nowait()
        assert second["type"] == "goal_complete"

    async def test_parse_failure_logged_and_loop_continues(self):
        svc = _svc()

        async def _async_gen():
            yield {"type": "pmessage", "data": "not json"}
            raise asyncio.CancelledError()

        mock_pubsub = AsyncMock()
        mock_pubsub.psubscribe = AsyncMock()
        mock_pubsub.listen = _async_gen

        mock_redis_ctx = AsyncMock()
        mock_redis_ctx.__aenter__ = AsyncMock(return_value=mock_redis_ctx)
        mock_redis_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_redis_ctx.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("redis.asyncio.from_url", return_value=mock_redis_ctx):
            task = asyncio.create_task(svc._subscribe_celery_goal_events("redis://x"))
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2.0)

    def test_start_celery_event_bridge_schedules_task(self):
        svc = _svc()

        async def _runner():
            with patch.object(
                svc, "_subscribe_celery_goal_events", AsyncMock(return_value=None)
            ):
                svc.start_celery_event_bridge("redis://x")
                await asyncio.sleep(0)
                assert svc._background_tasks

        asyncio.run(_runner())


# ── _run_agent_loop_persistent ────────────────────────────────────────────────


@dataclass
class _FakeStrategy:
    strategy_id: str = "react"
    adapter_version: str = "1.0.0"


@dataclass
class _FakePatterns:
    max_persistence_attempts: int = 4
    max_iterations: int = 8


@dataclass
class _FakeLimits:
    rounds: int = 4
    duration_seconds: float = 120.0


@dataclass
class _FakeRuntimeProfile:
    agent_patterns: _FakePatterns = field(default_factory=_FakePatterns)
    effective_limits: _FakeLimits = field(default_factory=_FakeLimits)
    primary_strategy: _FakeStrategy = field(default_factory=_FakeStrategy)
    profile_id: str = "prof-1"
    profile_version: int = 3


class TestRunAgentLoopPersistent:
    async def test_success_path_with_runtime_profile(self):
        svc = _svc()
        ctx = _ctx()
        record = _inject_goal(
            svc, "p1", ctx.tenant_id, runtime_profile=_FakeRuntimeProfile()
        )

        fake_engine = MagicMock()
        fake_engine.run = AsyncMock(return_value=(True, []))
        fake_engine.total_cost_usd = 0.01

        with patch(
            "app.agent.persistence.GoalPersistenceEngine", return_value=fake_engine
        ):
            await svc._run_agent_loop_persistent("p1", "goal text", ctx)

        assert record.status != GoalStatus.FAILED

    async def test_exhausted_attempts_dispatches_goal_failed(self):
        svc = _svc()
        ctx = _ctx()
        record = _inject_goal(svc, "p2", ctx.tenant_id)

        attempt = MagicMock()
        attempt.strategy = "react"
        fake_engine = MagicMock()
        fake_engine.run = AsyncMock(return_value=(False, [attempt]))
        fake_engine.total_cost_usd = 0.5

        with patch(
            "app.agent.persistence.GoalPersistenceEngine", return_value=fake_engine
        ):
            await svc._run_agent_loop_persistent("p2", "goal text", ctx)

        events = [e.get("type") for e in record.events]
        assert "goal_failed" in events

    async def test_cancelled_error_marks_cancelled_and_reraises(self):
        svc = _svc()
        ctx = _ctx()
        record = _inject_goal(svc, "p3", ctx.tenant_id)

        fake_engine = MagicMock()
        fake_engine.run = AsyncMock(side_effect=asyncio.CancelledError())

        with patch(
            "app.agent.persistence.GoalPersistenceEngine", return_value=fake_engine
        ):
            with pytest.raises(asyncio.CancelledError):
                await svc._run_agent_loop_persistent("p3", "goal text", ctx)

        assert record.status == GoalStatus.CANCELLED

    async def test_generic_exception_dispatches_goal_failed(self):
        svc = _svc()
        ctx = _ctx()
        record = _inject_goal(svc, "p5", ctx.tenant_id)

        fake_engine = MagicMock()
        fake_engine.run = AsyncMock(side_effect=RuntimeError("kaboom"))

        with patch(
            "app.agent.persistence.GoalPersistenceEngine", return_value=fake_engine
        ):
            await svc._run_agent_loop_persistent("p5", "goal text", ctx)

        events = [e.get("type") for e in record.events]
        assert "goal_failed" in events


# ── _run_agent_loop_isolated ───────────────────────────────────────────────────


class TestRunAgentLoopIsolated:
    def _patch_common(self, scheduler):
        return patch.multiple(
            "app.execution_environment.scheduler",
            ExecutionEnvironmentScheduler=MagicMock(
                from_flags=MagicMock(return_value=scheduler)
            ),
        )

    async def test_success_marks_complete(self):
        svc = _svc()
        ctx = _ctx()
        record = _inject_goal(svc, "iso1", ctx.tenant_id)
        svc._app_state = MagicMock()
        svc._app_state.execution_scheduler = None

        result = MagicMock(success=True, error_message="")
        scheduler = MagicMock()
        scheduler.schedule = AsyncMock(return_value=result)

        with patch(
            "app.execution_environment.scheduler.ExecutionEnvironmentScheduler.from_flags",
            return_value=scheduler,
        ):
            await svc._run_agent_loop_isolated("iso1", "goal text", ctx)

        assert record.status == GoalStatus.COMPLETE

    async def test_failure_marks_failed_with_error_message(self):
        svc = _svc()
        ctx = _ctx()
        record = _inject_goal(svc, "iso2", ctx.tenant_id)
        svc._app_state = MagicMock()
        svc._app_state.execution_scheduler = None

        result = MagicMock(success=False, error_message="oops")
        scheduler = MagicMock()
        scheduler.schedule = AsyncMock(return_value=result)

        with patch(
            "app.execution_environment.scheduler.ExecutionEnvironmentScheduler.from_flags",
            return_value=scheduler,
        ):
            await svc._run_agent_loop_isolated("iso2", "goal text", ctx)

        assert record.status == GoalStatus.FAILED
        assert record.error_message == "oops"

    async def test_runner_unavailable_error_dispatches_isolation_failed_event(self):
        from app.execution_environment.models import ExecutionFailureReason
        from app.execution_environment.scheduler import RunnerUnavailableError

        svc = _svc()
        ctx = _ctx()
        record = _inject_goal(svc, "iso3", ctx.tenant_id)
        svc._app_state = MagicMock()
        svc._app_state.execution_scheduler = MagicMock()

        exc = RunnerUnavailableError(
            "no runner", failure_reason=ExecutionFailureReason.RUNNER_UNAVAILABLE
        )
        scheduler = svc._app_state.execution_scheduler
        scheduler.schedule = AsyncMock(side_effect=exc)

        await svc._run_agent_loop_isolated("iso3", "goal text", ctx)

        assert record.status == GoalStatus.FAILED
        events = [e.get("type") for e in record.events]
        assert "goal_failed" in events

    async def test_generic_exception_marks_failed(self):
        svc = _svc()
        ctx = _ctx()
        record = _inject_goal(svc, "iso4", ctx.tenant_id)
        svc._app_state = MagicMock()
        svc._app_state.execution_scheduler = MagicMock()
        svc._app_state.execution_scheduler.schedule = AsyncMock(
            side_effect=ValueError("bad envelope")
        )

        await svc._run_agent_loop_isolated("iso4", "goal text", ctx)

        assert record.status == GoalStatus.FAILED
        events = [e.get("type") for e in record.events]
        assert "goal_failed" in events


# ── submit_goal auto-routing ───────────────────────────────────────────────────


class TestSubmitGoalAutoRouting:
    async def test_prewired_agent_router_confident_decision_sets_agent_id(self):
        from app.agent.router import RoutingDecision

        svc = _svc()
        ctx = _ctx("cb-router-1")
        app_state = MagicMock()
        agent_store = MagicMock()
        app_state.agent_store = agent_store
        decision = RoutingDecision(
            agent_id="agent-42", reason="best match", confidence=0.9, mode="single_agent"
        )
        app_state.agent_router = MagicMock()
        app_state.agent_router.route = AsyncMock(return_value=decision)
        svc._app_state = app_state

        result = await svc.submit_goal(
            goal="please route me", priority="normal", dry_run=True, tenant_ctx=ctx
        )
        assert result["agent_id"] == "agent-42"

    async def test_multi_agent_decision_spawns_candidates(self):
        from app.agent.router import RoutingDecision

        svc = _svc()
        ctx = _ctx("cb-router-2")
        app_state = MagicMock()
        app_state.agent_store = MagicMock()
        decision = RoutingDecision(
            agent_id=None,
            reason="fan out",
            confidence=0.9,
            mode="multi_agent",
            candidate_agents=[
                {"agent_id": "a1"},
                {"agent_id": "a2"},
            ],
        )
        app_state.agent_router = MagicMock()
        app_state.agent_router.route = AsyncMock(return_value=decision)
        svc._app_state = app_state

        async def _fake_submit_single(*, goal, agent_id, tenant_ctx, priority, dry_run):
            return {"goal_id": f"gid-{agent_id}", "agent_id": agent_id}

        with patch.object(
            svc, "_submit_single_goal", AsyncMock(side_effect=_fake_submit_single)
        ):
            result = await svc.submit_goal(
                goal="fan out please",
                priority="normal",
                dry_run=True,
                tenant_ctx=ctx,
            )

        assert result["mode"] == "multi_agent"
        assert set(result["goal_ids"]) == {"gid-a1", "gid-a2"}

    async def test_router_failure_falls_back_to_best_scored_agent(self):
        svc = _svc()
        ctx = _ctx("cb-router-3")
        app_state = MagicMock()
        agent_store = MagicMock()
        agent_store.list = MagicMock(
            return_value=[
                {"agent_id": "low", "name": "low", "goal_template": "unrelated"},
                {"agent_id": "hi", "name": "hi", "goal_template": "fan out please"},
            ]
        )
        app_state.agent_store = agent_store
        app_state.agent_router = None
        svc._app_state = app_state

        with patch(
            "app.agent.router.AgentRouter.route",
            AsyncMock(side_effect=RuntimeError("route failed")),
        ):
            result = await svc.submit_goal(
                goal="fan out please",
                priority="normal",
                dry_run=True,
                tenant_ctx=ctx,
            )

        assert result["agent_id"] in {"low", "hi"}

    async def test_dedup_returns_existing_goal_id(self):
        svc = _svc()
        ctx = _ctx("cb-dedup-1")

        fake_dedup = MagicMock()
        fake_dedup.get_existing = AsyncMock(return_value="existing-goal-id")
        fake_dedup.register = AsyncMock()

        with patch("app.services.dedup._default_deduplicator", fake_dedup):
            result = await svc.submit_goal(
                goal="dup me",
                priority="normal",
                dry_run=True,
                tenant_ctx=ctx,
            )

        assert result["goal_id"] == "existing-goal-id"
        assert result["deduplicated"] is True


# ── DB-backed list_goals / get_metrics / _db_persist_step ──────────────────────


class TestListGoalsDbBacked:
    async def test_list_goals_queries_db_and_merges_memory_event_count(self):
        svc = _svc()
        ctx = _ctx("cb-list-1")
        db, session = _make_mock_db()

        row = MagicMock()
        row.id = "dbgoal-1"
        row.status = "executing"
        row.goal_text = "hello"
        row.priority = "normal"
        row.dry_run = False
        row.agent_id = None
        row.workflow_mode = "single_agent"
        row.created_at = datetime.now(UTC)

        session.execute = AsyncMock(
            return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[row]))))
        )
        svc._db = db

        with patch.object(
            svc, "_batch_event_counts", AsyncMock(return_value={"dbgoal-1": 2})
        ):
            result = await svc.list_goals(ctx, limit=10, offset=0)

        assert result["goals"][0]["goal_id"] == "dbgoal-1"
        assert result["goals"][0]["event_count"] == 2


class TestGetMetricsDbBacked:
    async def test_get_metrics_computes_from_db_row(self):
        svc = _svc()
        ctx = _ctx("cb-metrics-1")
        db, session = _make_mock_db()

        # completed, failed, active, cancelled, completed_today, avg_latency_ms, submitted_today
        row = (2, 1, 1, 0, 2, 500.0, 3)
        session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=row)))
        svc._db = db

        result = await svc.get_metrics(ctx)

        assert result["completed_goals"] == 2
        assert result["failed_goals"] == 1
        assert result["success_rate"] == pytest.approx(2 / 3, rel=1e-3)

    async def test_get_metrics_db_error_falls_back_to_memory(self):
        svc = _svc()
        ctx = _ctx("cb-metrics-2")
        db = MagicMock()
        db.side_effect = RuntimeError("db down")
        svc._db = db
        _inject_goal(svc, "m1", ctx.tenant_id, status="complete")

        result = await svc.get_metrics(ctx)
        assert result["total_goals"] >= 1


class TestDbPersistStep:
    async def test_persist_step_writes_row_when_db_configured(self):
        svc = _svc()
        db, session = _make_mock_db()
        svc._db = db

        await svc._db_persist_step(
            goal_id="g1",
            tenant_id="cb-t1",
            step_index=0,
            description="do it",
            status="complete",
            output={"ok": True},
        )
        assert session.add.called

    async def test_persist_step_noop_when_no_db(self):
        svc = _svc()
        svc._db = None
        # Should return early without raising.
        await svc._db_persist_step(
            goal_id="g1",
            tenant_id="cb-t1",
            step_index=0,
            description="do it",
            status="complete",
            output=None,
        )


# ── _db_persist_goal / _db_ensure_goal_row ─────────────────────────────────────


class TestDbPersistGoal:
    async def test_persist_goal_writes_row_when_db_configured(self):
        svc = _svc()
        db, session = _make_mock_db()
        svc._db = db

        await svc._db_persist_goal(
            goal_id="g1",
            tenant_id="cb-t1",
            goal_text="hello",
            status="planning",
            priority="normal",
            dry_run=False,
        )
        assert session.add.called

    async def test_persist_goal_swallows_db_error_by_default(self):
        svc = _svc()
        db = MagicMock()
        db.side_effect = RuntimeError("db exploded")
        svc._db = db

        # Should not raise (raise_on_error defaults to False).
        await svc._db_persist_goal(
            goal_id="g1",
            tenant_id="cb-t1",
            goal_text="hello",
            status="planning",
            priority="normal",
            dry_run=False,
        )

    async def test_persist_goal_reraises_when_raise_on_error(self):
        svc = _svc()
        db = MagicMock()
        db.side_effect = RuntimeError("db exploded")
        svc._db = db

        with pytest.raises(RuntimeError):
            await svc._db_persist_goal(
                goal_id="g1",
                tenant_id="cb-t1",
                goal_text="hello",
                status="planning",
                priority="normal",
                dry_run=False,
                raise_on_error=True,
            )


class TestDbEnsureGoalRow:
    async def test_ensure_goal_row_noop_when_no_db(self):
        svc = _svc()
        svc._db = None
        await svc._db_ensure_goal_row(
            goal_id="g1",
            tenant_id="cb-t1",
            goal_text="hello",
            status="planning",
            priority="normal",
            dry_run=False,
        )

    async def test_ensure_goal_row_skips_when_row_already_exists(self):
        svc = _svc()
        svc._db = MagicMock()  # non-None so the "no db" early-return is skipped

        with (
            patch.object(svc, "_db_get_goal_record", AsyncMock(return_value=object())),
            patch.object(svc, "_db_persist_goal", AsyncMock()) as persist_mock,
        ):
            await svc._db_ensure_goal_row(
                goal_id="g1",
                tenant_id="cb-t1",
                goal_text="hello",
                status="planning",
                priority="normal",
                dry_run=False,
            )
        persist_mock.assert_not_called()

    async def test_ensure_goal_row_persists_when_missing(self):
        svc = _svc()
        svc._db = MagicMock()

        with (
            patch.object(svc, "_db_get_goal_record", AsyncMock(return_value=None)),
            patch.object(svc, "_db_persist_goal", AsyncMock()) as persist_mock,
        ):
            await svc._db_ensure_goal_row(
                goal_id="g1",
                tenant_id="cb-t1",
                goal_text="hello",
                status="planning",
                priority="normal",
                dry_run=False,
            )
        persist_mock.assert_awaited_once()


# ── _register_tools_from_context / _build_tool_context ────────────────────────


class TestRegisterToolsFromContext:
    def test_registers_discovered_tool_names(self):
        from app.services.goal_service import GoalService as _GS

        checker = MagicMock()
        loop = MagicMock()
        loop._guardrail_checker = checker

        tool = MagicMock()
        tool.name = "browser_click"
        tool_context = MagicMock()
        tool_context.tools = [tool]

        _GS._register_tools_from_context(loop, tool_context)
        checker.register_tools.assert_called_once()
        (registered_names,), _ = checker.register_tools.call_args
        assert "browser_click" in registered_names

    def test_noop_when_no_checker_or_context(self):
        from app.services.goal_service import GoalService as _GS

        loop = MagicMock()
        loop._guardrail_checker = None
        # Should not raise.
        _GS._register_tools_from_context(loop, MagicMock())
        _GS._register_tools_from_context(MagicMock(_guardrail_checker=MagicMock()), None)

    def test_registration_exception_is_swallowed(self):
        from app.services.goal_service import GoalService as _GS

        checker = MagicMock()
        checker.register_tools = MagicMock(side_effect=RuntimeError("boom"))
        loop = MagicMock()
        loop._guardrail_checker = checker

        tool = MagicMock()
        tool.name = "x"
        tool_context = MagicMock()
        tool_context.tools = [tool]

        # Should not raise even though register_tools blows up.
        _GS._register_tools_from_context(loop, tool_context)


class TestBuildToolContextToolSelector:
    async def test_tool_selector_success_path_uses_tiered_prompt(self):
        svc = _svc()
        ctx = _ctx("cb-tools-1")
        app_state = MagicMock()
        agent_store = MagicMock()
        agent_store.get = MagicMock(return_value={"connector_ids": []})
        app_state.agent_store = agent_store
        mcp_client = MagicMock()
        app_state.mcp_client = mcp_client
        svc._app_state = app_state
        svc._agent_store = agent_store

        selection = MagicMock()
        selection.selected = []
        tool_selector = MagicMock()
        tool_selector.select = AsyncMock(return_value=selection)
        app_state.tool_selector = tool_selector

        with patch(
            "app.agent.tool_context.to_tiered_prompt", return_value="tiered!"
        ):
            result = await svc._build_tool_context(
                agent_id="agent-1", tenant_ctx=ctx, goal="do the thing"
            )

        assert result.tool_prompt_override == "tiered!"

    async def test_tool_selector_failure_falls_back_to_full_tools(self):
        svc = _svc()
        ctx = _ctx("cb-tools-2")
        app_state = MagicMock()
        agent_store = MagicMock()
        agent_store.get = MagicMock(return_value={"connector_ids": []})
        app_state.agent_store = agent_store
        app_state.mcp_client = MagicMock()
        svc._app_state = app_state
        svc._agent_store = agent_store

        tool_selector = MagicMock()
        tool_selector.select = AsyncMock(side_effect=RuntimeError("selector broke"))
        app_state.tool_selector = tool_selector

        result = await svc._build_tool_context(
            agent_id="agent-1", tenant_ctx=ctx, goal="do the thing"
        )
        # Falls back to the full (unselected) tool list — RPA tools at minimum.
        assert len(result.tools) > 0


# ── subscribe_events: cross-replica live pub/sub loop ──────────────────────────


class TestSubscribeEventsCrossReplicaLive:
    async def test_since_sequence_replay_then_live_terminal_event(self):
        svc = _svc()
        ctx = _ctx("cb-sse-1")
        goal_id = "cross-replica-goal-1"

        db_record = _inject_goal(svc, goal_id, ctx.tenant_id, status="executing")
        # Simulate the record NOT being local to this replica.
        del svc._goals[goal_id]
        svc._redis_url_for_pubsub = "redis://x"

        async def _async_gen():
            yield {
                "type": "message",
                "data": json.dumps({"type": "step_started", "payload": {}}),
            }
            yield {
                "type": "message",
                "data": "not-json",  # parse failure -> continue
            }
            yield {
                "type": "message",
                "data": json.dumps({"type": "goal_complete", "payload": {}}),
            }
            # Should never reach this due to the terminal-event break above.
            yield {"type": "message", "data": json.dumps({"type": "unreachable"})}

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.listen = _async_gen
        mock_pubsub.__aenter__ = AsyncMock(return_value=mock_pubsub)
        mock_pubsub.__aexit__ = AsyncMock(return_value=False)

        mock_redis_ctx = AsyncMock()
        mock_redis_ctx.__aenter__ = AsyncMock(return_value=mock_redis_ctx)
        mock_redis_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_redis_ctx.pubsub = MagicMock(return_value=mock_pubsub)

        with (
            patch.object(svc, "_db_get_goal_record", AsyncMock(return_value=db_record)),
            patch.object(
                svc, "_list_events_since_persisted", AsyncMock(return_value=[{"_seq": 1}])
            ),
            patch("redis.asyncio.from_url", return_value=mock_redis_ctx),
        ):
            events = [
                e async for e in svc.subscribe_events(goal_id, ctx, since_sequence=1)
            ]

        types = [e.get("type") for e in events if "type" in e]
        assert types[0] == "step_started"
        assert "goal_complete" in types
        assert "unreachable" not in types

    async def test_no_redis_url_returns_after_replay(self):
        svc = _svc()
        ctx = _ctx("cb-sse-2")
        goal_id = "cross-replica-goal-2"
        db_record = _inject_goal(svc, goal_id, ctx.tenant_id, status="executing")
        del svc._goals[goal_id]
        svc._redis_url_for_pubsub = ""

        with (
            patch.object(svc, "_db_get_goal_record", AsyncMock(return_value=db_record)),
            patch.object(svc, "_list_persisted_events", AsyncMock(return_value=[])),
        ):
            events = [e async for e in svc.subscribe_events(goal_id, ctx)]

        assert events == []

    async def test_terminal_db_record_returns_after_replay_no_subscribe(self):
        svc = _svc()
        ctx = _ctx("cb-sse-3")
        goal_id = "cross-replica-goal-3"
        db_record = _inject_goal(svc, goal_id, ctx.tenant_id, status="complete")
        del svc._goals[goal_id]
        svc._redis_url_for_pubsub = "redis://x"

        with (
            patch.object(svc, "_db_get_goal_record", AsyncMock(return_value=db_record)),
            patch.object(svc, "_list_persisted_events", AsyncMock(return_value=[{"type": "goal_complete"}])),
        ):
            events = [e async for e in svc.subscribe_events(goal_id, ctx)]

        assert [e.get("type") for e in events] == ["goal_complete"]

    async def test_redis_error_is_caught_and_logged(self):
        svc = _svc()
        ctx = _ctx("cb-sse-4")
        goal_id = "cross-replica-goal-4"
        db_record = _inject_goal(svc, goal_id, ctx.tenant_id, status="executing")
        del svc._goals[goal_id]
        svc._redis_url_for_pubsub = "redis://x"

        with (
            patch.object(svc, "_db_get_goal_record", AsyncMock(return_value=db_record)),
            patch.object(svc, "_list_persisted_events", AsyncMock(return_value=[])),
            patch("redis.asyncio.from_url", side_effect=RuntimeError("conn refused")),
        ):
            events = [e async for e in svc.subscribe_events(goal_id, ctx)]

        assert events == []


# ── _make_agent_loop_for_tenant: per-tenant LLM provider dispatch ─────────────


class TestMakeAgentLoopForTenantProviderDispatch:
    def test_anthropic_tenant_config_builds_anthropic_provider(self):
        svc = _svc()
        ctx = _ctx("cb-provider-1")
        app_state = MagicMock()
        app_state._llm_provider_override = None
        app_state._llm_configs = {
            ctx.tenant_id: {
                "provider": "anthropic",
                "encrypted_key": "enc-blob",
                "default_model": "claude-opus-4-8",
            }
        }

        fake_vault = MagicMock()
        fake_vault.decrypt = MagicMock(return_value="sk-ant-real-key")

        with (
            patch("app.providers.vault.get_vault", return_value=fake_vault),
        ):
            loop = svc._make_agent_loop_for_tenant(ctx, app_state)

        assert loop is not None
        inner = getattr(loop._planner, "_inner", loop._planner)
        assert type(inner).__name__ == "AnthropicProvider"

    def test_openai_compatible_tenant_config_builds_provider(self):
        svc = _svc()
        ctx = _ctx("cb-provider-2")
        app_state = MagicMock()
        app_state._llm_provider_override = None
        app_state._llm_configs = {
            ctx.tenant_id: {
                "provider": "openai",
                "encrypted_key": "enc-blob",
                "default_model": "gpt-5.2",
                "base_url": None,
            }
        }

        fake_vault = MagicMock()
        fake_vault.decrypt = MagicMock(return_value="sk-openai-real-key")

        with patch("app.providers.vault.get_vault", return_value=fake_vault):
            loop = svc._make_agent_loop_for_tenant(ctx, app_state)

        inner = getattr(loop._planner, "_inner", loop._planner)
        assert type(inner).__name__ == "OpenAICompatibleProvider"

    def test_vault_decrypt_failure_falls_back_gracefully(self):
        svc = _svc()
        ctx = _ctx("cb-provider-3")
        app_state = MagicMock()
        app_state._llm_provider_override = None
        app_state._llm_configs = {
            ctx.tenant_id: {"provider": "anthropic", "encrypted_key": "enc-blob"}
        }

        with patch(
            "app.providers.vault.get_vault", side_effect=RuntimeError("vault down")
        ):
            loop = svc._make_agent_loop_for_tenant(ctx, app_state)

        # No API key resolved -> falls through to the default (Fake) provider.
        assert loop is not None


# ── _run_agent_loop_persistent: agent_factory closure ─────────────────────────


class TestRunAgentLoopPersistentAgentFactory:
    async def test_agent_factory_wraps_agent_with_tool_context(self):
        """Exercises the agent_factory() closure: collection-id lookup,
        guardrail-allowlist population, tool registration, and the
        _WrappedAgent.run() seam that injects tool_prompt/tool_context."""
        svc = _svc()
        ctx = _ctx("cb-factory-1")
        record = _inject_goal(svc, "pf1", ctx.tenant_id)
        record.agent_id = "agent-9"

        fake_loop = MagicMock()
        fake_loop._guardrail_checker = MagicMock()
        fake_loop.run = AsyncMock(return_value=None)

        fake_agent_store = MagicMock()
        fake_agent_store.get = MagicMock(
            return_value={"allowed_collection_ids": ["c1", "c2"]}
        )

        tool_context = MagicMock()
        tool_context.to_prompt_block = MagicMock(return_value="tool prompt")

        captured: dict[str, Any] = {}

        async def _engine_run(*, goal, agent_factory, tenant_ctx, event_callback, goal_id):
            built_agent = agent_factory()
            captured["agent"] = built_agent
            await built_agent.run(
                goal=goal, tenant_ctx=tenant_ctx, event_callback=event_callback
            )
            return True, []

        fake_engine = MagicMock()
        fake_engine.run = AsyncMock(side_effect=_engine_run)

        with (
            patch.object(svc, "_make_agent_loop_for_tenant", return_value=fake_loop),
            patch.object(svc, "_get_agent_store", return_value=fake_agent_store),
            patch.object(svc, "_register_tools_from_context") as register_mock,
            patch(
                "app.agent.persistence.GoalPersistenceEngine", return_value=fake_engine
            ),
        ):
            await svc._run_agent_loop_persistent(
                "pf1", "goal text", ctx, tool_context=tool_context
            )

        assert fake_loop._agent_collection_ids == ["c1", "c2"]
        register_mock.assert_called_once()
        fake_loop.run.assert_awaited_once()
        # The wrapped agent is not the raw loop (it's the _WrappedAgent shim).
        assert captured["agent"] is not fake_loop


# ── get_pattern_selection ──────────────────────────────────────────────────────


class TestGetPatternSelection:
    async def test_returns_stored_pattern_selection_record(self):
        svc = _svc()
        ctx = _ctx("cb-pattern-1")
        record = _inject_goal(svc, "pat1", ctx.tenant_id, status="executing")
        record.execution_context["pattern_selection"] = {
            "pattern": "react", "reason": "default"
        }

        result = await svc.get_pattern_selection("pat1", ctx)
        assert result["pattern"] == "react"
        assert result["goal_id"] == "pat1"

    async def test_recomputes_summary_when_missing(self):
        svc = _svc()
        ctx = _ctx("cb-pattern-2")
        _inject_goal(svc, "pat2", ctx.tenant_id, status="planning")

        with patch(
            "app.orchestration.pattern_selection_summary.summarize_pattern_selection",
            return_value={"pattern": "computed", "reason": "fallback"},
        ):
            result = await svc.get_pattern_selection("pat2", ctx)

        assert result["pattern"] == "computed"

    async def test_raises_not_found_for_unknown_goal(self):
        from app.core.errors import NotFoundError

        svc = _svc()
        ctx = _ctx("cb-pattern-3")

        with patch.object(svc, "_db_get_goal_record", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await svc.get_pattern_selection("nope", ctx)
