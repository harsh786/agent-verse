"""MEM-52: a suite run executes every golden goal ON the agent it evaluates.

Golden goals were submitted with no agent_id (auto-routed) and a run recorded
no agent, so the rollout gate could promote an agent on a run that never
exercised it.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.intelligence.eval_suite import EvalSuiteRunner, GoldenTask
from app.intelligence.rollout_gate import agent_config_hash
from app.main import create_app
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="bind", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _Svc:
    def __init__(self) -> None:
        self.submits: list[dict[str, Any]] = []

    async def submit_goal(self, **kw: Any) -> dict[str, Any]:
        self.submits.append(kw)
        return {"goal_id": f"g{len(self.submits)}"}

    async def subscribe_events(self, *, goal_id: str, tenant_ctx: Any) -> Any:
        yield {"type": "tool_call_complete", "tool_name": "t", "output": "ok"}
        yield {"type": "goal_complete"}

    async def cancel_goal(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        return {}


async def test_runner_submits_every_goal_with_the_agent() -> None:
    svc = _Svc()
    tasks = [GoldenTask(goal=f"g{i}", expected_tools=["t"]) for i in range(3)]
    result = await EvalSuiteRunner().run_suite("s", svc, _CTX, tasks=tasks, agent_id="agent-a")
    assert result.passed_tasks == 3
    assert [s["agent_id"] for s in svc.submits] == ["agent-a"] * 3


async def test_a_changed_agent_config_stops_the_remaining_tasks() -> None:
    svc = _Svc()
    calls = {"n": 0}

    async def pin_check() -> str | None:
        calls["n"] += 1
        return None if calls["n"] == 1 else "the agent's configuration changed during the run"

    tasks = [GoldenTask(goal=f"g{i}", expected_tools=["t"]) for i in range(3)]
    result = await EvalSuiteRunner().run_suite(
        "s", svc, _CTX, tasks=tasks, agent_id="agent-a", pin_check=pin_check, concurrency=1
    )
    assert len(svc.submits) == 1
    assert result.passed_tasks == 1 and result.unscored_tasks == 2
    assert all(
        "configuration changed" in r.failure_reasons[0]
        for r in result.task_results if not r.passed
    )


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.core.config import get_settings
    from tests.intelligence._eval_fakes import FakeGoals

    monkeypatch.setattr(get_settings(), "eval_suite_goal_poll_seconds", 0.01)
    app = create_app()
    svc = FakeGoals()
    app.state.goal_service = svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "T", "email": "bind@t.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        c.svc = svc  # type: ignore[attr-defined]
        yield c


async def test_run_against_an_agent_records_it_and_its_config(client: AsyncClient) -> None:
    agent = (await client.post("/agents", json={"name": "a", "system_prompt": "p"})).json()
    sid = (await client.post("/intelligence/eval-suites", json={})).json()["suite_id"]
    await client.post(f"/intelligence/eval-suites/{sid}/tasks",
                      json={"goal": "g", "expected_tools": ["t"]})
    r = await client.post(f"/intelligence/eval-suites/{sid}/run",
                          json={"agent_id": agent["agent_id"]})
    assert r.status_code == 202, r.text
    record = (await client.get(f"/agents/{agent['agent_id']}")).json()
    assert r.json()["agent_config_hash"] == agent_config_hash(record)

    import asyncio

    for _ in range(300):
        runs = (await client.get(f"/intelligence/eval-suites/{sid}/results")).json()
        if runs and runs[0]["status"] != "running":
            break
        await asyncio.sleep(0.05)
    assert runs[0]["agent_id"] == agent["agent_id"]
    assert runs[0]["agent_config_hash"] == agent_config_hash(record)
    assert client.svc.submits[0]["agent_id"] == agent["agent_id"]  # type: ignore[attr-defined]


async def test_run_against_an_unknown_agent_is_404(client: AsyncClient) -> None:
    sid = (await client.post("/intelligence/eval-suites", json={})).json()["suite_id"]
    await client.post(f"/intelligence/eval-suites/{sid}/tasks",
                      json={"goal": "g", "expected_tools": ["t"]})
    r = await client.post(f"/intelligence/eval-suites/{sid}/run", json={"agent_id": "nope"})
    assert r.status_code == 404


def test_config_hash_ignores_name_and_autonomy_but_not_behaviour() -> None:
    base = {"name": "a", "system_prompt": "p", "connector_ids": ["x", "y"],
            "autonomy_mode": "bounded-autonomous", "max_iterations": 15}
    same = {**base, "name": "b", "autonomy_mode": "fully-autonomous",
            "connector_ids": ["y", "x"]}
    assert agent_config_hash(base) == agent_config_hash(same)
    for change in ({"system_prompt": "q"}, {"model_override": "m"},
                   {"connector_ids": ["x"]}, {"policy_ids": ["p1"]},
                   {"pattern_flags": {"enable_cot": True}}):
        assert agent_config_hash({**base, **change}) != agent_config_hash(base)
