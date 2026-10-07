"""a10-F237-01..06: Agent Runtime traces are a view of the goal, not a shadow copy.

* F237-01: only the API process that ran the goal updated the trace; a
  Celery-run goal's trace never got an outcome or duration.
* F237-02: those updates ended in a bare ``except Exception: pass``.
* F237-03: nothing ever set total_cost_usd / total_tokens / role_calls /
  model_selections.
* F237-04: POST /plans and /traces accepted any goal_id (another tenant's too).
* F237-05: /strategies was a static list naming modes nothing runs.
* F237-06: a trace the LRU / Redis store lost (TTL, outage, other replica) was a 404.

Outcome now comes from the goal record and cost from goal_cost_breakdowns, both
written by whichever process ran the goal; a lost trace id resolves from the
goal's persisted execution_context.
"""

from __future__ import annotations

import uuid

import inspect
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_runtime.models import AgentRunTrace
from app.agent_runtime.store import AgentRuntimeStore
from app.api import agent_runtime as ar
from app.core.errors import NotFoundError
from app.observability import cost_breakdown
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t-ar", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "ak_test_agent_runtime_view"
H = {"X-API-Key": _KEY}


class _Goals:
    """GoalService stand-in holding the canonical goal records of tenant t-ar."""

    def __init__(self) -> None:
        self.goals: dict[str, dict[str, Any]] = {}
        self.trace_links: dict[str, str] = {}

    async def get_goal_outcome(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
        if tenant_ctx.tenant_id != _CTX.tenant_id or goal_id not in self.goals:
            raise NotFoundError(f"Goal not found: {goal_id}")
        return {"goal_id": goal_id, **self.goals[goal_id]}

    async def find_goals_by_context(
        self, tenant_ctx: TenantContext, key: str, value: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        assert key == "agent_runtime_trace_id"
        gid = self.trace_links.get(value)
        return [{"goal_id": gid, "status": "x", "created_at": ""}] if gid else []


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _Goals, AgentRuntimeStore]:
    store = AgentRuntimeStore()
    monkeypatch.setattr(ar, "agent_runtime_store", store)
    goals = _Goals()
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(ar.router)
    app.state.goal_service = goals
    yield TestClient(app, raise_server_exceptions=False), goals, store
    cost_breakdown._goal_breakdowns.clear()


async def test_worker_run_goal_trace_reports_outcome_and_cost(env: Any) -> None:
    client, goals, store = env
    # A unique goal id: cost_breakdown is process-global and other tests record
    # role costs under short ids like "g1", which inflated the totals here.
    gid = f"g-{uuid.uuid4().hex}"
    started = datetime.now(UTC) - timedelta(seconds=90)
    goals.goals[gid] = {
        "status": "executing", "created_at": started.isoformat(),
        "completed_at": None, "failure_reason": None,
    }
    # Created at submit (as GoalService does) and never touched again: the goal
    # then runs and finishes on a Celery worker.
    await store.put_trace(AgentRunTrace(trace_id="tr1", goal_id=gid, tenant_id=_CTX.tenant_id))
    cost_breakdown.record_role_cost(gid, "planner", "model-a", 100, 20, 0.01)
    cost_breakdown.record_role_cost(gid, "executor", "model-b", 300, 50, 0.03)
    cost_breakdown.record_role_cost(gid, "executor", "model-b", 10, 5, 0.002)

    running = client.get("/agent-runtime/traces/tr1", headers=H).json()
    assert running["status"] == "executing" and running["success"] is False
    assert running["duration_ms"] >= 89_000

    goals.goals[gid].update(
        status="complete", completed_at=(started + timedelta(seconds=60)).isoformat()
    )
    done = client.get("/agent-runtime/traces/tr1", headers=H).json()
    assert done["success"] is True and done["error"] is None
    assert done["duration_ms"] == pytest.approx(60_000, abs=1)
    assert done["total_tokens"] == 485
    assert done["total_cost_usd"] == pytest.approx(0.042)
    assert {(c["role"], c["calls"]) for c in done["role_calls"]} == {
        ("planner", 1), ("executor", 2)
    }
    assert done["model_selections"] == [
        {"role": "planner", "model": "model-a"}, {"role": "executor", "model": "model-b"}
    ]


def test_failed_goal_trace_carries_the_sanitized_reason(env: Any) -> None:
    client, goals, store = env
    goals.goals["g2"] = {
        "status": "failed", "created_at": datetime.now(UTC).isoformat(),
        "completed_at": datetime.now(UTC).isoformat(), "failure_reason": "budget exhausted",
    }
    goals.trace_links["tr-lost"] = "g2"  # the store no longer has this trace
    body = client.get("/agent-runtime/traces/tr-lost", headers=H).json()
    assert body["trace_id"] == "tr-lost" and body["goal_id"] == "g2"
    assert body["success"] is False and body["error"] == "budget exhausted"


def test_unknown_trace_is_404(env: Any) -> None:
    client, _goals, _store = env
    assert client.get("/agent-runtime/traces/nope", headers=H).status_code == 404


@pytest.mark.parametrize("path", ["/agent-runtime/plans", "/agent-runtime/traces"])
def test_plans_and_traces_refuse_a_goal_the_tenant_does_not_own(env: Any, path: str) -> None:
    client, goals, _store = env
    goals.goals["mine"] = {"status": "planning", "created_at": "", "completed_at": None,
                           "failure_reason": None}
    assert client.post(path, json={"goal_id": "someone-elses"}, headers=H).status_code == 404
    assert client.post(path, json={"goal_id": "mine"}, headers=H).status_code == 200
    assert client.post(path, json={}, headers=H).status_code == 200  # unlinked


def test_strategies_are_the_workflow_modes_goals_run(env: Any) -> None:
    client, _goals, _store = env
    body = client.get("/agent-runtime/strategies", headers=H).json()
    ids = [s["id"] for s in body["strategies"]]
    assert ids == ["single_agent", "multi_agent", "debate", "supervisor"]
    assert body["strategy_catalogue"] == "/strategies"
    # Every advertised mode is one the goals API / GoalService branches on.
    from app.api import goals as goals_api
    from app.services import goal_service

    src = inspect.getsource(goals_api) + inspect.getsource(goal_service)
    for mode in ids[1:]:
        assert f'"{mode}"' in src


def test_goal_service_no_longer_swallows_trace_updates() -> None:
    from app.services import goal_service

    src = inspect.getsource(goal_service.GoalService._dispatch_event)
    assert "agent_runtime_store.update_trace" not in src


async def test_goal_service_outcome_comes_from_the_goal_record() -> None:
    from app.governance.audit import AuditLog
    from app.governance.hitl import HITLGateway
    from app.services.goal_service import GoalRecord, GoalService, GoalStatus

    svc = GoalService(audit_log=AuditLog(), hitl=HITLGateway())
    svc._goals["g9"] = GoalRecord(
        goal_id="g9", goal_text="x", status=GoalStatus.FAILED, tenant_id=_CTX.tenant_id,
        priority="normal", dry_run=False, created_at="2026-10-07T00:00:00+00:00",
        completed_at="2026-10-07T00:01:00+00:00",
        error_message="LLM budget exhausted for tenant",
    )
    out = await svc.get_goal_outcome("g9", _CTX)
    assert out["status"] == "failed" and out["completed_at"].startswith("2026-10-07T00:01")
    assert out["failure_reason"]
    other = TenantContext(tenant_id="t-other", plan=PlanTier.FREE, api_key_id="k")
    with pytest.raises(NotFoundError):
        await svc.get_goal_outcome("g9", other)


async def test_trace_role_calls_name_the_serving_model_and_the_failed_over_one(env: Any) -> None:
    """ONPREM-ROUTING-FAILOVER: the dead pinned model is provenance, never "served"."""
    client, goals, store = env
    gid = f"g-{uuid.uuid4().hex}"
    now = datetime.now(UTC)
    goals.goals[gid] = {
        "status": "complete", "created_at": now.isoformat(), "completed_at": now.isoformat(),
        "failure_reason": None,
    }
    await store.put_trace(AgentRunTrace(trace_id="tr-fo", goal_id=gid, tenant_id=_CTX.tenant_id))
    cost_breakdown.record_role_cost(gid, "executor", "Qwen/Qwen3.5-4B", 30, 3, 0.0,
                                    fallback_from=["rw-dead-closed-port"])
    cost_breakdown.record_role_cost(gid, "planner", "Qwen/Qwen3.5-4B", 30, 3, 0.0)

    trace = client.get("/agent-runtime/traces/tr-fo", headers=H).json()

    by_role = {c["role"]: c for c in trace["role_calls"]}
    assert by_role["executor"]["model"] == "Qwen/Qwen3.5-4B"
    assert by_role["executor"]["fallback_from"] == ["rw-dead-closed-port"]
    assert by_role["planner"]["fallback_from"] == []
    served = {c["model"] for c in trace["role_calls"]} | {
        s["model"] for s in trace["model_selections"]
    }
    assert "rw-dead-closed-port" not in served
    assert {"role": "executor", "model": "Qwen/Qwen3.5-4B",
            "fallback_from": ["rw-dead-closed-port"]} in trace["model_selections"]
