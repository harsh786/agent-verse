"""a08-F200-03 / a08-F200-02 (owner decisions).

* A goal TIMEOUT undoes its registered side effects, through the same path as
  the verifier's permanent failure (``rollback_goal_side_effects``).
* Cancel and emergency stop undo them only when asked (``rollback=true``,
  audited); the default keeps what was done.
* A resumed (crashed / requeued) goal gets its undo records back from the OI-1
  action ledger, so a later failure / timeout / cancel-with-rollback still
  undoes the first attempt's calls — once. A crash alone undoes nothing.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import fakeredis
import fakeredis.aioredis
import pytest

from app.agent.state import GoalStatus
from app.reliability.goal_lifecycle import (
    GoalCancelledError,
    cancel_rollback_requested,
    cancel_rollback_requested_sync,
    signal_cancel,
    withdraw_cancel,
)
from app.reliability.rollback import (
    RollbackEngine,
    find_rollback_engine,
    rehydrate_from_ledger,
    rollback_goal_side_effects,
)
from app.reliability.tool_inverses import _INVERSE_REGISTRY, ROLLED_BACK, InverseResult
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-rb", plan=PlanTier.PROFESSIONAL, api_key_id="k-op")
TOOL = "acme_create_ticket"


@pytest.fixture
def undone(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def _inverse(payload: dict[str, Any], client: Any) -> InverseResult:
        calls.append(payload)
        return InverseResult(ROLLED_BACK, "deleted")

    monkeypatch.setitem(_INVERSE_REGISTRY, TOOL, _inverse)
    return calls


def _engine_with_one_call() -> RollbackEngine:
    engine = RollbackEngine()
    engine.register_tool_call(
        action="file a ticket", tool_names=[TOOL], arguments={"title": "x"},
        output='{"id": "T-1"}', server_id="srv", tenant_ctx=CTX,
    )
    return engine


# ── the shared path ───────────────────────────────────────────────────────────


async def test_rollback_runs_once_and_reports(undone: list[dict[str, Any]]) -> None:
    engine = _engine_with_one_call()
    events: list[dict[str, Any]] = []

    async def _emit(e: dict[str, Any]) -> None:
        events.append(e)

    first = await rollback_goal_side_effects(engine, trigger="timeout", emit=_emit)
    second = await rollback_goal_side_effects(engine, trigger="cancel", emit=_emit)
    assert first is not None and first["counts"]["rolled_back"] == 1
    assert second is None  # nothing left: inverses run once
    assert len(undone) == 1 and undone[0]["result"] == '{"id": "T-1"}'
    assert events == [{"type": "rollback_report", **first}]
    assert events[0]["trigger"] == "timeout"


def test_find_rollback_engine_walks_runner_wrappers() -> None:
    engine = RollbackEngine()
    graph = MagicMock(spec=[])
    graph._rollback_engine = engine
    wrapper = MagicMock(spec=[])
    wrapper._runner = graph
    assert find_rollback_engine(wrapper) is engine
    assert find_rollback_engine(object()) is None


# ── opt-in cancel / stop flags ────────────────────────────────────────────────


async def test_cancel_rollback_flag_is_opt_in_and_withdrawn_with_the_cancel() -> None:
    server = fakeredis.FakeServer()
    r = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    sync_r = fakeredis.FakeRedis(server=server, decode_responses=True)
    await signal_cancel("g-plain", r)
    assert not await cancel_rollback_requested("g-plain", r)
    await signal_cancel("g-rb", r, rollback=True)
    assert await cancel_rollback_requested("g-rb", r)
    assert cancel_rollback_requested_sync("g-rb", sync_r)
    await withdraw_cancel("g-rb", r)
    assert not await cancel_rollback_requested("g-rb", r)


async def test_emergency_stop_records_the_rollback_request() -> None:
    from app.governance.emergency_stop import (
        activate_org_stop,
        activate_stop,
        stop_requests_rollback,
        stop_requests_rollback_sync,
        tenant_stop_key,
    )

    server = fakeredis.FakeServer()
    r = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    sync_r = fakeredis.FakeRedis(server=server, decode_responses=True)
    await activate_stop(r, tenant_stop_key("t-a"), activated_by="op")
    assert not await stop_requests_rollback(r, "t-a", None)
    await activate_stop(r, tenant_stop_key("t-b"), activated_by="op", rollback=True)
    assert await stop_requests_rollback(r, "t-b", None)
    assert stop_requests_rollback_sync(sync_r, "t-b", None)
    await activate_org_stop(r, "t-c", "org-1", activated_by="op", rollback=True)
    assert await stop_requests_rollback(r, "t-c", "org-1")
    assert not await stop_requests_rollback(r, "t-c", "org-2")


# ── worker: timeout / cancel ──────────────────────────────────────────────────


async def test_worker_timeout_undoes_side_effects(
    undone: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.reliability.active_budget import ActiveTimeBudget, run_within_active_budget
    from app.scaling import tasks

    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    events: list[dict[str, Any]] = []

    async def _emit(e: dict[str, Any]) -> None:
        events.append(e)

    budget = ActiveTimeBudget(0.1)
    with pytest.raises(TimeoutError):
        await tasks._run_with_abort_rollback(
            run_within_active_budget(asyncio.sleep(5), budget),
            _engine_with_one_call(),
            goal_id="g-t", tenant_id=CTX.tenant_id, org_id=None, emit=_emit,
        )
    assert len(undone) == 1
    assert [e["trigger"] for e in events if e["type"] == "rollback_report"] == ["timeout"]


@pytest.mark.parametrize(("rollback", "expected"), [(False, 0), (True, 1)])
async def test_worker_cancel_undoes_only_when_asked(
    undone: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    rollback: bool,
    expected: int,
) -> None:
    from app.scaling import tasks

    server = fakeredis.FakeServer()
    r = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(
        tasks, "_get_sync_redis", lambda: fakeredis.FakeRedis(server=server, decode_responses=True)
    )
    await signal_cancel("g-c", r, rollback=rollback)

    async def _cancelled_run() -> None:
        raise GoalCancelledError("Goal g-c cancelled")

    with pytest.raises(GoalCancelledError):
        await tasks._run_with_abort_rollback(
            _cancelled_run(), _engine_with_one_call(),
            goal_id="g-c", tenant_id=CTX.tenant_id, org_id=None, emit=None,
        )
    assert len(undone) == expected


async def test_worker_emergency_stop_with_rollback_undoes(
    undone: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.governance.emergency_stop import activate_stop, tenant_stop_key
    from app.scaling import tasks

    server = fakeredis.FakeServer()
    r = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(
        tasks, "_get_sync_redis", lambda: fakeredis.FakeRedis(server=server, decode_responses=True)
    )
    await activate_stop(r, tenant_stop_key(CTX.tenant_id), activated_by="op", rollback=True)

    async def _stopped_run() -> None:
        raise GoalCancelledError("Goal g-s stopped: emergency stop")

    with pytest.raises(GoalCancelledError):
        await tasks._run_with_abort_rollback(
            _stopped_run(), _engine_with_one_call(),
            goal_id="g-s", tenant_id=CTX.tenant_id, org_id=None, emit=None,
        )
    assert len(undone) == 1


# ── in-process (API) runner ───────────────────────────────────────────────────


class _HangingLoop:
    def __init__(self) -> None:
        self._agent_timeout_seconds = 0.05
        self._rollback_engine = _engine_with_one_call()

    async def run(self, **kwargs: Any) -> None:
        await asyncio.sleep(10)


async def test_in_process_timeout_undoes_side_effects(undone: list[dict[str, Any]]) -> None:
    from app.services.goal_service import GoalService

    class _Svc(GoalService):
        def _make_agent_loop_for_tenant(self, *args: Any, **kwargs: Any) -> _HangingLoop:
            return _HangingLoop()

    svc = _Svc()
    result = await svc.submit_goal(goal="hang", priority="normal", dry_run=False, tenant_ctx=CTX)
    record = svc._goals[result["goal_id"]]
    await asyncio.wait_for(record.task, timeout=5.0)
    assert record.status == GoalStatus.FAILED
    assert len(undone) == 1
    assert any(e.get("type") == "rollback_report" and e.get("trigger") == "timeout"
               for e in record.events)


@pytest.mark.parametrize(("rollback", "expected"), [(False, 0), (True, 1)])
async def test_in_process_cancel_undoes_only_when_asked(
    undone: list[dict[str, Any]], rollback: bool, expected: int
) -> None:
    from app.governance.audit import AuditLog
    from app.services.goal_service import GoalService

    class _Loop(_HangingLoop):
        def __init__(self) -> None:
            super().__init__()
            self._agent_timeout_seconds = 30

    class _Svc(GoalService):
        def _make_agent_loop_for_tenant(self, *args: Any, **kwargs: Any) -> _Loop:
            return _Loop()

    audit = AuditLog()
    svc = _Svc(audit_log=audit)
    result = await svc.submit_goal(goal="hang", priority="normal", dry_run=False, tenant_ctx=CTX)
    record = svc._goals[result["goal_id"]]
    await asyncio.sleep(0.05)
    await svc.cancel_goal(result["goal_id"], CTX, rollback=rollback)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(record.task, timeout=5.0)
    assert len(undone) == expected
    audited = [e for e in audit.query(tenant_ctx=CTX) if e.outcome == "rollback_requested"]
    assert len(audited) == expected


async def test_cancel_with_rollback_refused_when_it_cannot_be_audited() -> None:
    from app.core.errors import ServiceUnavailableError
    from app.services.goal_service import GoalService

    class _BrokenAudit:
        async def record_async(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("audit store down")

    class _Svc(GoalService):
        def _make_agent_loop_for_tenant(self, *args: Any, **kwargs: Any) -> Any:
            loop = _HangingLoop()
            loop._agent_timeout_seconds = 30
            return loop

    svc = _Svc(audit_log=_BrokenAudit())  # type: ignore[arg-type]
    result = await svc.submit_goal(goal="hang", priority="normal", dry_run=False, tenant_ctx=CTX)
    with pytest.raises(ServiceUnavailableError):
        await svc.cancel_goal(result["goal_id"], CTX, rollback=True)
    record = svc._goals[result["goal_id"]]
    assert record.status != GoalStatus.CANCELLED  # nothing changed
    record.task.cancel()
    await asyncio.gather(record.task, return_exceptions=True)


# ── resume rehydration (F200-02) ──────────────────────────────────────────────


def test_rehydrate_registers_without_undoing(undone: list[dict[str, Any]]) -> None:
    engine = RollbackEngine()
    entries = [
        {"tool": TOOL, "server_id": "srv", "arguments": json.dumps({"title": "b"}),
         "output": '{"id": "T-2"}', "at": 2.0},
        {"tool": TOOL, "server_id": "srv", "arguments": '{"title": "a", "trunc',  # truncated
         "output": '{"id": "T-1"}', "at": 1.0},
        {"tool": "", "output": "ignored"},
    ]
    assert rehydrate_from_ledger(engine, entries, tenant_ctx=CTX) == 2
    assert len(engine) == 2
    assert undone == []  # a crash alone undoes nothing
    assert engine.preview() == [f"resumed:{TOOL}", f"resumed:{TOOL}"]


@pytest.mark.parametrize(("query", "expected"), [("", False), ("?rollback=true", True)])
def test_cancel_endpoint_rollback_param_is_opt_in(query: str, expected: bool) -> None:
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.goals import router as goals_router
    from app.tenancy.middleware import TenantMiddleware

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return CTX if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(goals_router)
    svc = MagicMock()
    svc.cancel_goal = AsyncMock(return_value={"goal_id": "g1", "status": "cancelled"})
    app.state.goal_service = svc
    resp = TestClient(app).post(f"/goals/g1/cancel{query}", headers={"X-API-Key": "k"})
    assert resp.status_code == 200, resp.text
    assert svc.cancel_goal.await_args.kwargs["rollback"] is expected
    assert resp.json().get("rollback_requested", False) is expected
