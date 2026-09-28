"""AI-Ops dataset runs must execute the agent and use the configured judge.

Regression for: ``actual_output`` defaulted to ``expected_output`` (lexical
similarity 1.0, every run passed without running anything) and created judges
were never used (``judge_model`` was only echoed).
"""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import ai_ops
from app.api.ai_ops import router as ai_ops_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-aiops-honest", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
_KEY = "ak_aiops_honest"
_H = {"X-API-Key": _KEY}


class _FakeGoalService:
    """Answers each goal from ``answers``; ``fail`` goals emit goal_failed."""

    def __init__(self, answers: dict[str, str], fail: set[str] | None = None) -> None:
        self.answers = answers
        self.fail = fail or set()
        self.submitted: list[dict[str, Any]] = []
        self._goals: dict[str, str] = {}

    async def submit_goal(self, **kw: Any) -> dict[str, Any]:
        gid = f"g{len(self.submitted)}"
        self.submitted.append(kw)
        self._goals[gid] = kw["goal"]
        return {"goal_id": gid}

    async def subscribe_events(
        self, *, goal_id: str, tenant_ctx: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        goal = self._goals[goal_id]
        if goal in self.fail:
            yield {"type": "goal_failed", "reason": "tool exploded"}
            return
        yield {"type": "step_complete", "output": self.answers.get(goal, "")}
        yield {"type": "goal_complete"}


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[Any] = []

    async def complete(self, req: Any) -> _Resp:
        self.requests.append(req)
        return _Resp(self.content)


def _app(goal_service: Any = None, provider: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(ai_ops_router)
    if goal_service is not None:
        app.state.goal_service = goal_service
    if provider is not None:
        app.state._app_provider = provider
    return app


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    for d in (ai_ops._datasets, ai_ops._eval_results, ai_ops._judges, ai_ops._baselines):
        d.clear()
    ai_ops._drift_alerts.clear()


async def _run_and_wait(
    client: AsyncClient, app: FastAPI, dataset_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    started = await client.post(f"/ai-ops/datasets/{dataset_id}/run", json=body, headers=_H)
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "running"
    pending = list(app.state.__dict__.get("_ai_ops_run_tasks", set()))
    await asyncio.gather(*pending)
    res = await client.get(f"/ai-ops/eval-results/{started.json()['result_id']}", headers=_H)
    assert res.status_code == 200
    return dict(res.json())


async def _dataset(client: AsyncClient, tasks: list[dict[str, Any]]) -> str:
    r = await client.post("/ai-ops/datasets", json={"name": "d", "golden_tasks": tasks}, headers=_H)
    return str(r.json()["dataset_id"])


@pytest.mark.asyncio
async def test_run_executes_agent_and_never_substitutes_expected_for_actual() -> None:
    gs = _FakeGoalService({"Capital of France?": "London is the capital"})
    app = _app(gs)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        did = await _dataset(c, [{"input": "Capital of France?", "expected_output": "Paris"}])
        result = await _run_and_wait(c, app, did, {})

    assert [s["goal"] for s in gs.submitted] == ["Capital of France?"]
    assert gs.submitted[0]["tenant_ctx"].tenant_id == _CTX.tenant_id
    case = result["cases"][0]
    assert case["actual"] == "London is the capital"
    assert case["lexical_similarity"] == 0.0
    assert result["status"] == "completed"
    assert result["passed"] is False
    assert result["avg_score"] == 0.0


@pytest.mark.asyncio
async def test_run_passes_only_on_real_matching_output() -> None:
    gs = _FakeGoalService({"Capital of France?": "Paris"})
    app = _app(gs)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        did = await _dataset(c, [{"input": "Capital of France?", "expected_output": "Paris"}])
        result = await _run_and_wait(c, app, did, {})
    assert result["passed"] is True
    assert result["executed_cases"] == 1


@pytest.mark.asyncio
async def test_run_without_agent_execution_path_fails_honestly() -> None:
    app = _app(goal_service=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        did = await _dataset(c, [{"input": "q", "expected_output": "a"}])
        r = await c.post(f"/ai-ops/datasets/{did}/run", json={}, headers=_H)
        assert r.status_code == 503
        listed = await c.get("/ai-ops/eval-results", headers=_H)
    assert listed.json()["total"] == 0


@pytest.mark.asyncio
async def test_empty_dataset_is_rejected_not_scored() -> None:
    app = _app(_FakeGoalService({}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        did = await _dataset(c, [])
        r = await c.post(f"/ai-ops/datasets/{did}/run", json={}, headers=_H)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_failed_goal_fails_the_case() -> None:
    gs = _FakeGoalService({}, fail={"q"})
    app = _app(gs)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        did = await _dataset(c, [{"input": "q", "expected_output": "a"}])
        result = await _run_and_wait(c, app, did, {})
    case = result["cases"][0]
    assert case["status"] == "execution_failed"
    assert "tool exploded" in case["error"]
    assert result["passed"] is False
    assert result["executed_cases"] == 0


@pytest.mark.asyncio
async def test_configured_judge_scores_each_case() -> None:
    gs = _FakeGoalService({"q": "the agent answer"})
    provider = _FakeProvider('{"accuracy": 0.9, "safety": 0.8, "reasoning": "fine"}')
    app = _app(gs, provider)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        j = await c.post(
            "/ai-ops/judges",
            json={
                "name": "J",
                "provider": "anthropic",
                "model": "judge-model-x",
                "evaluation_dimensions": ["accuracy", "safety"],
            },
            headers=_H,
        )
        did = await _dataset(c, [{"input": "q", "expected_output": "ref"}])
        result = await _run_and_wait(c, app, did, {"judge_id": j.json()["judge_id"]})

    assert len(provider.requests) == 1
    req = provider.requests[0]
    assert req.model == "judge-model-x"
    assert "the agent answer" in req.messages[0].content
    case = result["cases"][0]
    assert case["judge_scores"] == {"accuracy": 0.9, "safety": 0.8}
    assert case["score"] == pytest.approx(0.85)
    assert result["scoring"] == "llm_judge"
    assert result["judge"]["judge_id"] == j.json()["judge_id"]
    assert result["scores"]["accuracy"] == 0.9
    assert result["passed"] is True


@pytest.mark.asyncio
async def test_judge_failure_is_recorded_not_papered_over() -> None:
    gs = _FakeGoalService({"q": "answer"})
    app = _app(gs, _FakeProvider("I refuse to output JSON"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        j = await c.post(
            "/ai-ops/judges", json={"name": "J", "provider": "p", "model": "m"}, headers=_H
        )
        did = await _dataset(c, [{"input": "q", "expected_output": "answer"}])
        result = await _run_and_wait(c, app, did, {"judge_id": j.json()["judge_id"]})
    case = result["cases"][0]
    assert case["status"] == "judge_error"
    assert result["judge_errors"] == 1
    assert result["passed"] is False


@pytest.mark.asyncio
async def test_unknown_judge_is_404_and_judge_without_provider_is_503() -> None:
    app = _app(_FakeGoalService({"q": "a"}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        did = await _dataset(c, [{"input": "q", "expected_output": "a"}])
        r = await c.post(f"/ai-ops/datasets/{did}/run", json={"judge_id": "nope"}, headers=_H)
        assert r.status_code == 404
        j = await c.post(
            "/ai-ops/judges", json={"name": "J", "provider": "p", "model": "m"}, headers=_H
        )
        r = await c.post(
            f"/ai-ops/datasets/{did}/run", json={"judge_id": j.json()["judge_id"]}, headers=_H
        )
        assert r.status_code == 503


@pytest.mark.asyncio
async def test_stale_running_result_reports_abandoned() -> None:
    app = _app(_FakeGoalService({}))
    old = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=3)).isoformat()
    ai_ops._eval_results[_CTX.tenant_id] = [
        {"result_id": "r-old", "status": "running", "created_at": old, "tenant_id": _CTX.tenant_id}
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/ai-ops/eval-results/r-old", headers=_H)
    assert r.json()["status"] == "abandoned"
    assert r.json()["passed"] is False
