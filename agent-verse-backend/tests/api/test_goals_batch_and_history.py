"""a10-F231-01/02/04/05/06: goal batch + history sub-endpoints tell the truth.

* ``POST /goals/batch`` returned a uuid ``batch_id`` that the status route split
  as a comma-separated goal-id list: it resolved to one ``not_found`` goal that
  counted as done, so ``all_complete`` was true at once. The batch id is now
  persisted on every goal (``execution_context.batch_id``) and resolved from it.
* ``max_parallel`` was accepted and ignored (goals were submitted one by one).
* traces / lineage / attempts answered ``[]`` / a root-only tree on any DB error,
  and lineage / attempts did not check that the goal exists.
* a stored 0.0 decision-trace confidence was read back as 0.5.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.core.errors import NotFoundError
from app.governance.audit import AuditLog
from app.governance.hitl import HITLGateway
from app.services.goal_service import GoalRecord, GoalService, GoalStatus
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t-batch", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_OTHER = TenantContext(tenant_id="t-batch-other", plan=PlanTier.PROFESSIONAL, api_key_id="k2")
_KEY = "ak_test_batch_history"
H = {"X-API-Key": _KEY}


def _client(svc: Any) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(goals_router)
    app.state.goal_service = svc
    return TestClient(app, raise_server_exceptions=False)


def _real_service_recording_submissions() -> GoalService:
    """A real GoalService whose submit only records the goal (no agent run)."""
    svc = GoalService(audit_log=AuditLog(), hitl=HITLGateway())
    counter = {"n": 0}

    async def _submit(
        *, goal: str, tenant_ctx: TenantContext, execution_context: Any = None, **_kw: Any
    ) -> dict[str, Any]:
        counter["n"] += 1
        gid = f"g{counter['n']:03d}"
        svc._goals[gid] = GoalRecord(
            goal_id=gid,
            goal_text=goal,
            status=GoalStatus.PLANNING,
            tenant_id=tenant_ctx.tenant_id,
            priority="normal",
            dry_run=False,
            created_at=datetime.now(UTC).isoformat(),
            execution_context=execution_context or {},
        )
        return {"goal_id": gid}

    svc.submit_goal = _submit  # type: ignore[method-assign]
    return svc


# ── batch ─────────────────────────────────────────────────────────────────────


def test_batch_submit_then_status_round_trip() -> None:
    svc = _real_service_recording_submissions()
    client = _client(svc)

    r = client.post("/goals/batch", json={"goals": ["a", "b", "c"]}, headers=H)
    assert r.status_code == 202
    body = r.json()
    batch_id = body["batch_id"]
    assert batch_id.startswith("batch_")
    assert [g["goal_id"] for g in body["goals"]] == ["g001", "g002", "g003"]

    status = client.get(f"/goals/batch/{batch_id}/status", headers=H).json()
    assert status["total"] == 3
    assert {g["goal_id"] for g in status["goals"]} == {"g001", "g002", "g003"}
    assert status["all_complete"] is False  # nothing finished yet

    for gid in ("g001", "g002"):
        svc._goals[gid].status = GoalStatus.COMPLETE
    svc._goals["g003"].status = GoalStatus.FAILED
    status = client.get(f"/goals/batch/{batch_id}/status", headers=H).json()
    assert status["all_complete"] is True


def test_batch_status_is_scoped_to_the_batch_and_tenant() -> None:
    svc = _real_service_recording_submissions()
    client = _client(svc)
    first = client.post("/goals/batch", json={"goals": ["a", "b"]}, headers=H).json()
    client.post("/goals/batch", json={"goals": ["c"]}, headers=H)
    # Same batch id on another tenant's goal must not leak into this tenant's view.
    svc._goals["foreign"] = GoalRecord(
        goal_id="foreign",
        goal_text="x",
        status=GoalStatus.PLANNING,
        tenant_id=_OTHER.tenant_id,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
        execution_context={"batch_id": first["batch_id"]},
    )
    status = client.get(f"/goals/batch/{first['batch_id']}/status", headers=H).json()
    assert {g["goal_id"] for g in status["goals"]} == {"g001", "g002"}


def test_unknown_batch_is_404_not_all_complete() -> None:
    svc = _real_service_recording_submissions()
    r = _client(svc).get("/goals/batch/batch_" + "0" * 32 + "/status", headers=H)
    assert r.status_code == 404


def test_batch_lookup_failure_is_503() -> None:
    svc = AsyncMock()
    svc.find_goals_by_context.side_effect = ConnectionError("db down")
    r = _client(svc).get("/goals/batch/batch_abc/status", headers=H)
    assert r.status_code == 503
    assert "db down" not in r.text


def test_batch_honours_max_parallel() -> None:
    svc = AsyncMock()
    state = {"running": 0, "peak": 0, "n": 0}

    async def _submit(**_kw: Any) -> dict[str, Any]:
        state["running"] += 1
        state["peak"] = max(state["peak"], state["running"])
        await asyncio.sleep(0.01)
        state["running"] -= 1
        state["n"] += 1
        return {"goal_id": f"g{state['n']}"}

    svc.submit_goal.side_effect = _submit
    r = _client(svc).post(
        "/goals/batch", json={"goals": [f"goal {i}" for i in range(8)], "max_parallel": 3},
        headers=H,
    )
    assert r.status_code == 202
    assert r.json()["queued"] == 8
    assert state["peak"] == 3  # bounded, and actually parallel (was always 1)
    # Each goal gets its own execution_context dict carrying the batch id.
    contexts = [c.kwargs["execution_context"] for c in svc.submit_goal.call_args_list]
    assert len({id(c) for c in contexts}) == 8
    assert {c["batch_id"] for c in contexts} == {r.json()["batch_id"]}


def test_legacy_comma_separated_status_still_works() -> None:
    svc = AsyncMock()
    svc.get_goal.side_effect = [{"status": "complete"}, NotFoundError("nope")]
    body = _client(svc).get("/goals/batch/g1,g2/status", headers=H).json()
    assert [g["status"] for g in body["goals"]] == ["complete", "not_found"]


# ── traces / lineage / attempts ───────────────────────────────────────────────


class _BrokenSession:
    async def __aenter__(self) -> _BrokenSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, *_a: Any, **_kw: Any) -> Any:
        raise ConnectionError("postgres://u:pw@db gone")


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _RowsSession(_BrokenSession):
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    async def execute(self, *_a: Any, **_kw: Any) -> Any:
        return _Rows(self._rows)


def _svc_with_db(session: Any) -> AsyncMock:
    svc = AsyncMock()
    svc._db = lambda: session
    return svc


def test_history_without_a_database_is_empty() -> None:
    svc = AsyncMock()
    svc._db = None
    svc.get_goal.return_value = {"goal_id": "g1"}
    client = _client(svc)
    assert client.get("/goals/g1/traces", headers=H).json() == []
    assert client.get("/goals/g1/attempts", headers=H).json() == []
    assert client.get("/goals/g1/lineage", headers=H).json()["nodes"][0]["goal_id"] == "g1"


@pytest.mark.parametrize("path", ["traces", "lineage", "attempts"])
def test_history_db_error_is_503_not_empty(path: str) -> None:
    svc = _svc_with_db(_BrokenSession())
    svc.get_goal.return_value = {"goal_id": "g1", "status": "complete"}
    r = _client(svc).get(f"/goals/g1/{path}", headers=H)
    assert r.status_code == 503
    assert "pw@db" not in r.text


@pytest.mark.parametrize("path", ["traces", "lineage", "attempts"])
def test_history_of_unknown_goal_is_404(path: str) -> None:
    svc = _svc_with_db(_RowsSession([]))
    svc.get_goal.side_effect = NotFoundError("Goal not found: other-tenants-goal")
    r = _client(svc).get(f"/goals/other-tenants-goal/{path}", headers=H)
    assert r.status_code == 404


def test_zero_confidence_trace_is_not_reported_as_half() -> None:
    at = datetime.now(UTC)
    svc = _svc_with_db(
        _RowsSession([("t1", "search", "r", 0.0, at), ("t2", "answer", "r", None, at)])
    )
    svc.get_goal.return_value = {"goal_id": "g1"}
    traces = _client(svc).get("/goals/g1/traces", headers=H).json()
    assert [t["confidence"] for t in traces] == [0.0, 0.5]


def test_lineage_and_attempts_read_real_rows() -> None:
    at = datetime.now(UTC)
    svc = _svc_with_db(
        _RowsSession(
            [
                ("l1", "root", "root", "child", None, "agent-c", None, "split", 1, at, "t"),
                ("root", "root", None, "root", None, None, None, "", 0, at, "t"),
            ]
        )
    )
    svc.get_goal.return_value = {"goal_id": "root"}
    lineage = _client(svc).get("/goals/root/lineage", headers=H).json()
    assert [n["goal_id"] for n in lineage["nodes"]] == ["root", "child"]
    assert lineage["edges"] == [{"parent": "root", "child": "child"}]

    svc._db = lambda: _RowsSession(
        [("a1", 1, "direct", "g", at, at, False, "timeout", 5, 0.25, 30.0)]
    )
    attempts = _client(svc).get("/goals/root/attempts", headers=H).json()
    assert attempts[0]["attempt_number"] == 1
    assert attempts[0]["failure_reason"] == "timeout"
    assert attempts[0]["cost_usd"] == 0.25


# ── multi-agent fan-out keeps the caller's context ────────────────────────────


async def test_multi_agent_fanout_children_keep_execution_context() -> None:
    svc = GoalService(audit_log=AuditLog(), hitl=HITLGateway())
    seen: list[dict[str, Any]] = []

    async def _single(**kw: Any) -> dict[str, Any]:
        seen.append(kw["execution_context"])
        return {"goal_id": f"g-{kw['agent_id']}", "agent_id": kw["agent_id"]}

    svc._submit_single_goal = _single  # type: ignore[method-assign]
    ctx = {"batch_id": "batch_x"}
    out = await svc._submit_multi_agent_routing(
        "goal",
        {"mode": "multi_agent", "candidate_agents": [{"agent_id": "a1"}, {"agent_id": "a2"}]},
        _CTX,
        priority="normal",
        dry_run=True,
        execution_context=ctx,
    )
    assert out is not None and out["goal_ids"] == ["g-a1", "g-a2"]
    assert [c["batch_id"] for c in seen] == ["batch_x", "batch_x"]
    assert seen[0] is not seen[1] and seen[0] is not ctx  # independent copies
