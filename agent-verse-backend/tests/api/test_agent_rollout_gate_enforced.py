"""MEM-22: the agent rollout gate reads the named eval suite and is enforced.

``check_agent_rollout_gate`` ignored ``eval_suite_id`` (it averaged every
evaluation of the agent's goals with a hard-coded 0.8) and nothing called it
when an agent was made fully-autonomous, so the gate was advisory only.
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


def _seed_run(ctx: TenantContext, suite_id: str, *, passed: int, total: int) -> None:
    async def _go() -> None:
        store = EvalSuiteStore(None, ctx.tenant_id)
        await store.create(suite_id, name=suite_id, description="")
        run_id = uuid.uuid4().hex
        await store.start_run(suite_id, run_id, total)
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


def test_switching_to_fully_autonomous_with_a_failing_suite_is_409() -> None:
    client, ctx = _client()
    _seed_run(ctx, "suite-weak", passed=1, total=4)
    agent_id = _agent(client, eval_suite_id="suite-weak")
    r = client.put(
        f"/agents/{agent_id}", json={"autonomy_mode": "fully-autonomous"}, headers=_h()
    )
    assert r.status_code == 409, r.text
    gate = r.json()["detail"]["gate"]
    assert gate["gate_passed"] is False
    assert gate["eval_suite_id"] == "suite-weak"
    assert gate["pass_rate"] == 0.25 and gate["min_pass_rate_required"] == 0.8
    # The agent was not changed.
    assert client.get(f"/agents/{agent_id}", headers=_h()).json()["autonomy_mode"] != (
        "fully-autonomous"
    )


def test_switching_with_a_passing_suite_is_allowed() -> None:
    client, ctx = _client()
    _seed_run(ctx, "suite-good", passed=5, total=5)
    agent_id = _agent(client, eval_suite_id="suite-good")
    r = client.put(
        f"/agents/{agent_id}", json={"autonomy_mode": "fully-autonomous"}, headers=_h()
    )
    assert r.status_code == 200, r.text
    assert r.json()["autonomy_mode"] == "fully-autonomous"


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
    _seed_run(ctx, "suite-other", passed=5, total=5)  # a passing, unrelated suite
    _seed_run(ctx, "suite-mine", passed=3, total=5)
    agent_id = _agent(client, eval_suite_id="suite-mine")
    body = client.get(f"/agents/{agent_id}/rollout-gate", headers=_h()).json()
    assert body["eval_suite_id"] == "suite-mine"
    assert body["pass_rate"] == 0.6 and body["gate_passed"] is False
    lenient = client.get(
        f"/agents/{agent_id}/rollout-gate?min_pass_rate=0.5", headers=_h()
    ).json()
    assert lenient["gate_passed"] is True and lenient["min_pass_rate_required"] == 0.5
