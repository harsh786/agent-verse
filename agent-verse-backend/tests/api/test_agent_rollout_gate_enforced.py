"""MEM-22 / MEM-52: the rollout gate reads the named suite's run AGAINST THIS AGENT.

MEM-22: ``check_agent_rollout_gate`` ignored ``eval_suite_id`` and nothing called
it when an agent was made fully-autonomous. MEM-52: it accepted the suite's
latest run whoever it exercised (golden goals ran without an agent), however
small the suite and however the agent changed since. A run now vouches only for
the agent it ran on, with the config hash and dataset version it ran with, over
at least ``rollout_min_suite_size`` tasks.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agents import AgentStore
from app.api.agents import router as agents_router
from app.intelligence.eval_suite import EvalSuiteResult, GoldenTaskResult
from app.intelligence.eval_suite_store import EvalSuiteStore
from app.intelligence.rollout_gate import agent_config_hash
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_KEY = "av_test_rollout_gate"


def _client() -> tuple[TestClient, TenantContext]:
    ctx = TenantContext(
        tenant_id=f"t-gate-{uuid.uuid4().hex[:8]}", plan=PlanTier.PROFESSIONAL, api_key_id="k"
    )
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(agents_router)
    app.state.agent_store = AgentStore()
    app.state.meta_agent = AsyncMock()
    return TestClient(app, raise_server_exceptions=False), ctx


def _seed_run(
    ctx: TenantContext,
    suite_id: str,
    *,
    passed: int,
    total: int,
    agent: dict[str, Any] | None = None,
) -> None:
    """A completed run of ``suite_id`` (``total`` golden tasks) against ``agent``."""

    async def _go() -> None:
        store = EvalSuiteStore(None, ctx.tenant_id)
        if await store.get_meta(suite_id) is None:
            await store.create(suite_id, name=suite_id, description="")
            await store.import_tasks(
                suite_id,
                [{"goal": f"g{i}", "expected_tools": ["t"]} for i in range(total)],
                replace=False,
            )
        meta = await store.get_meta(suite_id)
        assert meta is not None
        run_id = uuid.uuid4().hex
        await store.start_run(
            suite_id, run_id, total, dataset_version=meta["dataset_version"],
            agent_id=agent["agent_id"] if agent else None,
            agent_config_hash=agent_config_hash(agent) if agent else None,
        )
        result = EvalSuiteResult(
            suite_id=suite_id, run_id=run_id, total_tasks=total,
            passed_tasks=passed, failed_tasks=total - passed,
            task_results=[
                GoldenTaskResult(task_id=f"t{i}", goal="g", passed=i < passed)
                for i in range(total)
            ],
        )
        await store.finish_run(suite_id, run_id, result=result)

    asyncio.run(_go())


def _h() -> dict[str, str]:
    return {"X-API-Key": _KEY}


def _agent(client: TestClient, **extra: Any) -> str:
    r = client.post("/agents", json={"name": "a", **extra}, headers=_h())
    assert r.status_code == 201, r.text
    return str(r.json()["agent_id"])


def _record(client: TestClient, agent_id: str) -> dict[str, Any]:
    r = client.get(f"/agents/{agent_id}", headers=_h())
    assert r.status_code == 200
    data: dict[str, Any] = r.json()
    return data


def _promote(client: TestClient, agent_id: str, **extra: Any) -> Any:
    return client.put(
        f"/agents/{agent_id}", json={"autonomy_mode": "fully-autonomous", **extra}, headers=_h()
    )


def test_switching_to_fully_autonomous_with_a_failing_suite_is_409() -> None:
    client, ctx = _client()
    agent_id = _agent(client, eval_suite_id="suite-weak")
    _seed_run(ctx, "suite-weak", passed=1, total=5, agent=_record(client, agent_id))
    r = _promote(client, agent_id)
    assert r.status_code == 409, r.text
    gate = r.json()["detail"]["gate"]
    assert gate["gate_passed"] is False
    assert gate["eval_suite_id"] == "suite-weak"
    assert gate["pass_rate"] == 0.2 and gate["min_pass_rate_required"] == 0.8
    # The agent was not changed.
    assert client.get(f"/agents/{agent_id}", headers=_h()).json()["autonomy_mode"] != (
        "fully-autonomous"
    )


def test_switching_with_a_passing_run_against_this_agent_is_allowed() -> None:
    client, ctx = _client()
    agent_id = _agent(client, eval_suite_id="suite-good")
    _seed_run(ctx, "suite-good", passed=5, total=5, agent=_record(client, agent_id))
    r = _promote(client, agent_id)
    assert r.status_code == 200, r.text
    assert r.json()["autonomy_mode"] == "fully-autonomous"


def test_a_run_against_another_agent_never_opens_the_gate() -> None:
    client, ctx = _client()
    other = _agent(client, eval_suite_id="suite-x")
    mine = _agent(client, eval_suite_id="suite-x")
    _seed_run(ctx, "suite-x", passed=5, total=5, agent=_record(client, other))
    _seed_run(ctx, "suite-x", passed=5, total=5)  # auto-routed: vouches for nobody
    r = _promote(client, mine)
    assert r.status_code == 409, r.text
    assert "no completed run against agent" in r.json()["detail"]["gate"]["reason"]


def test_a_config_change_invalidates_the_run() -> None:
    client, ctx = _client()
    agent_id = _agent(client, eval_suite_id="suite-c", system_prompt="be careful")
    _seed_run(ctx, "suite-c", passed=5, total=5, agent=_record(client, agent_id))
    # Promoting while changing the prompt: the run did not exercise that prompt.
    r = _promote(client, agent_id, system_prompt="be reckless")
    assert r.status_code == 409, r.text
    assert "configuration changed" in r.json()["detail"]["gate"]["reason"]
    # An edit alone (still bounded) also closes the gate for the edited agent.
    assert client.put(f"/agents/{agent_id}", json={"model_override": "other-model"},
                      headers=_h()).status_code == 200
    gate = client.get(f"/agents/{agent_id}/rollout-gate", headers=_h()).json()
    assert gate["gate_passed"] is False and "configuration changed" in gate["reason"]


def test_a_dataset_edit_invalidates_the_run() -> None:
    client, ctx = _client()
    agent_id = _agent(client, eval_suite_id="suite-d")
    _seed_run(ctx, "suite-d", passed=5, total=5, agent=_record(client, agent_id))
    asyncio.run(
        EvalSuiteStore(None, ctx.tenant_id).add_task(
            "suite-d", {"goal": "new case", "expected_tools": ["t"]}
        )
    )
    r = _promote(client, agent_id)
    assert r.status_code == 409, r.text
    assert "golden dataset" in r.json()["detail"]["gate"]["reason"]


def test_a_suite_below_the_minimum_size_never_gates(monkeypatch: Any) -> None:
    client, ctx = _client()
    agent_id = _agent(client, eval_suite_id="suite-tiny")
    _seed_run(ctx, "suite-tiny", passed=2, total=2, agent=_record(client, agent_id))
    r = _promote(client, agent_id)
    assert r.status_code == 409, r.text
    assert "at least 5" in r.json()["detail"]["gate"]["reason"]

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "rollout_min_suite_size", 2)
    assert _promote(client, agent_id).status_code == 200


def test_a_suite_that_never_ran_blocks_creation_as_fully_autonomous() -> None:
    client, ctx = _client()
    r = client.post(
        "/agents",
        json={"name": "a", "autonomy_mode": "fully-autonomous", "eval_suite_id": "suite-none"},
        headers=_h(),
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "ROLLOUT_GATE_FAILED"


def test_gate_report_names_the_suite_and_threshold_and_ignores_other_suites() -> None:
    client, ctx = _client()
    agent_id = _agent(client, eval_suite_id="suite-mine")
    agent = _record(client, agent_id)
    _seed_run(ctx, "suite-other", passed=5, total=5, agent=agent)  # passing, unrelated
    _seed_run(ctx, "suite-mine", passed=3, total=5, agent=agent)
    body = client.get(f"/agents/{agent_id}/rollout-gate", headers=_h()).json()
    assert body["agent_config_hash"] == agent_config_hash(agent)
    assert body["run_agent_config_hash"] == body["agent_config_hash"]
    assert body["dataset_version"] == body["current_dataset_version"] == 1
    assert body["eval_suite_id"] == "suite-mine"
    assert body["pass_rate"] == 0.6 and body["gate_passed"] is False
    lenient = client.get(
        f"/agents/{agent_id}/rollout-gate?min_pass_rate=0.5", headers=_h()
    ).json()
    assert lenient["gate_passed"] is True and lenient["min_pass_rate_required"] == 0.5
