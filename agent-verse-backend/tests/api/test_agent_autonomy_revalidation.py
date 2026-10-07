"""Owner decision on a05-F095-04: a fully-autonomous agent's config change re-tests it.

PUT /agents/{id} that changes a fully-autonomous agent's behaviour config is
accepted: the agent is demoted to bounded-autonomous (marker reason
``config_changed_pending_eval``), its rollout-gate eval suite runs against the new
config, and the post-run hook promotes it back when the gate passes. A failing
run leaves it bounded with the result visible; an operator's manual autonomy
change (or a newer config change) means a stale run never re-promotes.

These run the real in-process durable run loop (no Celery) over the in-memory
stores with a scripted goal service.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agents import AgentStore
from app.api.agents import router as agents_router
from app.intelligence.eval_suite import EvalSuiteResult, GoldenTaskResult
from app.intelligence.eval_suite_store import EvalSuiteStore
from app.intelligence.rollout_gate import agent_config_hash
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from tests.intelligence._eval_fakes import FakeGoals

_KEY = "av_test_autonomy_revalidation"
_H = {"X-API-Key": _KEY}


class _Audit:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def record_async(self, event: Any, *, tenant_ctx: Any) -> None:
        self.events.append(event)

    def outcomes(self) -> list[str]:
        return [e.outcome for e in self.events]


@pytest.fixture(autouse=True)
def _fast_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "eval_suite_goal_poll_seconds", 0.01)


@contextmanager
def _client(
    outcome: Callable[[str, str | None], str] | None = None,
) -> Iterator[tuple[TestClient, TenantContext, FakeGoals, _Audit]]:
    ctx = TenantContext(
        tenant_id=f"t-reval-{uuid.uuid4().hex[:8]}", plan=PlanTier.PROFESSIONAL, api_key_id="k"
    )
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(agents_router)
    app.state.agent_store = AgentStore()
    app.state.meta_agent = AsyncMock()
    goals = FakeGoals(outcome)
    app.state.goal_service = goals
    audit = _Audit()
    app.state.audit_log = audit
    # A context-managed client keeps one event loop alive, so the in-process
    # eval run (an asyncio task started by the PUT) keeps running.
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, ctx, goals, audit


def _seed_suite_and_passing_run(ctx: TenantContext, suite_id: str, agent: dict[str, Any]) -> None:
    async def _go() -> None:
        store = EvalSuiteStore(None, ctx.tenant_id)
        await store.create(suite_id, name=suite_id, description="")
        await store.import_tasks(
            suite_id, [{"goal": f"g{i}", "expected_tools": ["t"]} for i in range(5)],
            replace=False,
        )
        run_id = uuid.uuid4().hex
        await store.start_run(suite_id, run_id, 5, dataset_version=1,
                              agent_id=agent["agent_id"],
                              agent_config_hash=agent_config_hash(agent))
        await store.finish_run(suite_id, run_id, result=EvalSuiteResult(
            suite_id=suite_id, run_id=run_id, total_tasks=5, passed_tasks=5, failed_tasks=0,
            task_results=[GoldenTaskResult(task_id=f"t{i}", goal="g", passed=True)
                          for i in range(5)],
        ))

    asyncio.run(_go())


def _fully_autonomous(client: TestClient, ctx: TenantContext) -> str:
    suite = f"suite-{uuid.uuid4().hex[:6]}"
    r = client.post("/agents", json={"name": "a", "eval_suite_id": suite,
                                     "system_prompt": "vetted"}, headers=_H)
    assert r.status_code == 201, r.text
    agent_id = str(r.json()["agent_id"])
    agent = client.get(f"/agents/{agent_id}", headers=_H).json()
    _seed_suite_and_passing_run(ctx, suite, agent)
    r = client.put(f"/agents/{agent_id}", json={"autonomy_mode": "fully-autonomous"}, headers=_H)
    assert r.status_code == 200, r.text
    return agent_id


def _wait_resolved(client: TestClient, agent_id: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body: dict[str, Any] = client.get(f"/agents/{agent_id}", headers=_H).json()
        if not body["pending_promotion"]:
            return body
        assert time.monotonic() < deadline, body
        time.sleep(0.05)


def test_a_passing_rerun_promotes_the_agent_back_automatically() -> None:
    with _client() as (client, _ctx, goals, audit):
        agent_id = _fully_autonomous(client, _ctx)
        r = client.put(f"/agents/{agent_id}",
                       json={"autonomy_mode": "fully-autonomous", "system_prompt": "new"},
                       headers=_H)
        assert r.status_code == 200, r.text
        body = r.json()
        # Accepted, written, demoted, re-validating.
        assert body["system_prompt"] == "new"
        assert body["autonomy_mode"] == "bounded-autonomous"
        assert body["pending_promotion"] is True
        marker = body["autonomy_revalidation"]
        assert marker["state"] == "pending"
        assert marker["reason"] == "config_changed_pending_eval"
        assert marker["source"] == "agent_update"
        assert marker["agent_config_hash"] == agent_config_hash(body)

        final = _wait_resolved(client, agent_id)
        assert final["autonomy_mode"] == "fully-autonomous"
        assert final["autonomy_revalidation"]["state"] == "promoted"
        assert final["autonomy_revalidation"]["run_id"] == marker["run_id"]
        # Every golden goal ran ON the agent, with the new config.
        assert len(goals.submits) == 5
        assert {s["agent_id"] for s in goals.submits} == {agent_id}
        assert audit.outcomes() == ["demoted", "promoted"]
        assert all(e.tool_name == "agent.autonomy" for e in audit.events)
        assert "config_changed_pending_eval" in audit.events[0].note
        # The gate now vouches for the new config.
        gate = client.get(f"/agents/{agent_id}/rollout-gate", headers=_H).json()
        assert gate["gate_passed"] is True and gate["run_id"] == marker["run_id"]


def test_a_failing_rerun_leaves_the_agent_bounded_with_the_result_visible() -> None:
    with _client(outcome=lambda _goal, _agent: "failed") as (client, ctx, _goals, audit):
        agent_id = _fully_autonomous(client, ctx)
        r = client.put(f"/agents/{agent_id}", json={"model_override": "m2"}, headers=_H)
        assert r.status_code == 200, r.text
        final = _wait_resolved(client, agent_id)
        assert final["autonomy_mode"] == "bounded-autonomous"
        marker = final["autonomy_revalidation"]
        assert marker["state"] == "failed"
        assert marker["pass_rate"] == 0.0
        assert "below" in marker["error"]
        assert audit.outcomes() == ["demoted", "revalidation_failed"]


def test_an_operator_autonomy_change_cancels_the_promotion() -> None:
    # Goals stay "executing" long enough for the operator to step in.
    with _client() as (client, ctx, goals, audit):
        goals.running_polls = 10_000
        agent_id = _fully_autonomous(client, ctx)
        marker = client.put(f"/agents/{agent_id}", json={"system_prompt": "new"},
                            headers=_H).json()["autonomy_revalidation"]
        assert marker["state"] == "pending"
        r = client.put(f"/agents/{agent_id}", json={"autonomy_mode": "supervised"}, headers=_H)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["autonomy_mode"] == "supervised"
        assert body["pending_promotion"] is False
        assert body["autonomy_revalidation"]["state"] == "cancelled"
        assert body["autonomy_revalidation"]["cancelled_by"] == "k"
        assert audit.outcomes() == ["demoted", "revalidation_cancelled"]
        # The run was failed, so even when its goals finish it promotes nothing.
        goals.running_polls = 0
        runs = asyncio.run(
            EvalSuiteStore(None, ctx.tenant_id).list_runs(marker["eval_suite_id"])
        )
        assert next(x for x in runs if x["run_id"] == marker["run_id"])["status"] == "failed"
        time.sleep(0.3)
        assert client.get(f"/agents/{agent_id}", headers=_H).json()["autonomy_mode"] == (
            "supervised"
        )


def test_a_newer_change_while_pending_supersedes_the_older_run() -> None:
    with _client() as (client, ctx, goals, _audit):
        goals.running_polls = 10_000
        agent_id = _fully_autonomous(client, ctx)
        first = client.put(f"/agents/{agent_id}", json={"system_prompt": "v2"},
                           headers=_H).json()["autonomy_revalidation"]
        # The UI re-sends the (now bounded) mode with the next edit: not a manual change.
        r = client.put(f"/agents/{agent_id}",
                       json={"autonomy_mode": "bounded-autonomous", "system_prompt": "v3"},
                       headers=_H)
        assert r.status_code == 200, r.text
        second = r.json()["autonomy_revalidation"]
        assert second["state"] == "pending"
        assert second["run_id"] != first["run_id"]
        assert second["token"] != first["token"]
        runs = {x["run_id"]: x for x in asyncio.run(
            EvalSuiteStore(None, ctx.tenant_id).list_runs(first["eval_suite_id"])
        )}
        assert runs[first["run_id"]]["status"] == "failed"
        goals.running_polls = 0
        final = _wait_resolved(client, agent_id)
        assert final["system_prompt"] == "v3"
        assert final["autonomy_mode"] == "fully-autonomous"
        assert final["autonomy_revalidation"]["run_id"] == second["run_id"]


def test_a_rename_of_a_pending_agent_does_not_restart_the_run() -> None:
    with _client() as (client, ctx, goals, _audit):
        goals.running_polls = 10_000
        agent_id = _fully_autonomous(client, ctx)
        first = client.put(f"/agents/{agent_id}", json={"system_prompt": "v2"},
                           headers=_H).json()["autonomy_revalidation"]
        body = client.put(f"/agents/{agent_id}", json={"name": "renamed"}, headers=_H).json()
        assert body["autonomy_revalidation"]["run_id"] == first["run_id"]
        assert body["pending_promotion"] is True


def test_reverting_to_a_config_a_run_already_vouches_for_keeps_it_fully_autonomous() -> None:
    with _client() as (client, ctx, _goals, audit):
        agent_id = _fully_autonomous(client, ctx)
        # Re-sending the vetted prompt is no change; nothing is demoted.
        r = client.put(f"/agents/{agent_id}", json={"system_prompt": "vetted"}, headers=_H)
        assert r.json()["autonomy_mode"] == "fully-autonomous"
        assert audit.outcomes() == []


def test_the_gate_switched_off_by_the_owner_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    with _client() as (client, ctx, _goals, audit):
        agent_id = _fully_autonomous(client, ctx)
        monkeypatch.setattr(get_settings(), "fully_autonomous_eval_gate_enabled", False)
        body = client.put(f"/agents/{agent_id}", json={"system_prompt": "new"},
                          headers=_H).json()
        assert body["autonomy_mode"] == "fully-autonomous"
        assert body["autonomy_revalidation"] is None
        assert audit.outcomes() == []


def test_snapshot_rollback_never_restores_a_revalidation_marker() -> None:
    with _client() as (client, ctx, goals, _audit):
        goals.running_polls = 10_000
        agent_id = _fully_autonomous(client, ctx)
        client.put(f"/agents/{agent_id}", json={"system_prompt": "v2"}, headers=_H)
        snap = client.post(f"/agents/{agent_id}/snapshot", headers=_H).json()
        assert snap["pending_promotion"] is True
        client.put(f"/agents/{agent_id}", json={"autonomy_mode": "supervised"}, headers=_H)
        r = client.post(f"/agents/{agent_id}/rollback/{snap['snapshot_id']}", headers=_H)
        assert r.status_code == 200, r.text
        body = client.get(f"/agents/{agent_id}", headers=_H).json()
        assert body["autonomy_revalidation"]["state"] == "cancelled"
        assert body["pending_promotion"] is False
