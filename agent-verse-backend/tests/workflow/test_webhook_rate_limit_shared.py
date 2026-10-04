"""The /wf-hooks rate limit uses the shared Redis sliding window (RATE-02 follow-up).

Regression: RATE-02 deleted the per-process ``RateLimiter`` class, but the
workflow webhook router still imported it, so EVERY webhook delivery answered
500 (ImportError). The router now uses ``SlidingWindowRateLimiter`` on the
app's rate-limit Redis — one window shared by every replica — and, while Redis
is unreachable, the middleware's per-replica share of the limit (never open).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from app.api.workflows import _WorkflowStore
from app.tenancy import middleware as tenancy_middleware
from app.tenancy.context import PLAN_LIMITS, PlanLimits, PlanTier, TenantContext
from app.workflow.router import router as wf_router
from app.workflow.service import WorkflowService
from app.workflow.webhook_router import router as hook_router

_T = "tenant-rl"


class _TokenStore:
    async def get_webhook_token_version(self, tenant_id: str, workflow_id: str) -> int:
        return 0

    async def rotate_webhook_token(self, tenant_id: str, workflow_id: str) -> int:
        return 1


class _Runner:
    def __init__(self, run_store: Any) -> None:
        self._run_store = run_store
        self.runs: list[dict[str, Any]] = []

    async def _get_plan_tier(self, tenant_id: str) -> str:
        return "free"

    async def run(self, **kw: Any) -> str:
        self.runs.append(kw)
        return f"run-{len(self.runs)}"


class _SharedRedis:
    """Stands in for the one Redis every replica shares: evaluates the
    sliding-window script as a per-key counter and records the keys."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.keys: list[str] = []

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        key = str(keys_and_args[0])
        limit = int(keys_and_args[numkeys + 2])
        self.keys.append(key)
        count = self.counts.get(key, 0)
        if count >= limit:
            return [0, 0]
        self.counts[key] = count + 1
        return [1, limit - count - 1]


class _DownRedis:
    async def eval(self, *_a: Any, **_k: Any) -> Any:
        raise ConnectionError("redis down")

    async def zremrangebyscore(self, *_a: Any, **_k: Any) -> Any:
        raise ConnectionError("redis down")


def _replica(svc: WorkflowService, runner: _Runner, redis: Any) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id=_T, plan=PlanTier.FREE, api_key_id="k", roles=("admin",)
        )
        request.app.state.workflow_service = svc
        request.app.state.workflow_runner = runner
        request.app.state._rate_limiter_redis = redis
        return await call_next(request)

    app.include_router(wf_router, prefix="/api/v1")
    app.include_router(hook_router)
    return TestClient(app)


async def _webhook_path(svc: WorkflowService, client: TestClient) -> str:
    wf = await svc.create(
        tenant_id=_T,
        name="hook-rl",
        definition={"name": "hook-rl", "steps": [], "trigger": {"type": "webhook"}},
    )
    wid = str(wf["id"])
    await svc.publish(tenant_id=_T, workflow_id=wid)
    return str(client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"])


@pytest.fixture
def svc_runner() -> tuple[WorkflowService, _Runner]:
    run_store = _TokenStore()
    return WorkflowService(store=_WorkflowStore(), run_store=run_store), _Runner(run_store)


@pytest.mark.asyncio
async def test_a_webhook_delivery_starts_a_run(
    svc_runner: tuple[WorkflowService, _Runner],
) -> None:
    svc, runner = svc_runner
    client = _replica(svc, runner, _SharedRedis())
    path = await _webhook_path(svc, client)

    resp = client.post(path, json={"x": 1})

    assert resp.status_code == 200, resp.text
    assert len(runner.runs) == 1


@pytest.mark.asyncio
async def test_two_replicas_share_one_webhook_window(
    svc_runner: tuple[WorkflowService, _Runner], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(PLAN_LIMITS, PlanTier.FREE, PlanLimits(2, 25, 3, 2, 1, 3600))
    svc, runner = svc_runner
    redis = _SharedRedis()
    a, b = _replica(svc, runner, redis), _replica(svc, runner, redis)
    path = await _webhook_path(svc, a)

    codes = [a.post(path, json={}).status_code, b.post(path, json={}).status_code]
    codes.append(a.post(path, json={}).status_code)
    codes.append(b.post(path, json={}).status_code)

    assert codes == [200, 200, 429, 429]
    assert len(runner.runs) == 2
    # One tenant-namespaced, per-workflow key in the shared Redis.
    assert len(set(redis.keys)) == 1
    key = redis.keys[0]
    assert key.startswith(f"tenant:{_T}:") and "wfhook:" in key


@pytest.mark.asyncio
async def test_redis_outage_enforces_the_replica_share_not_open(
    svc_runner: tuple[WorkflowService, _Runner], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(PLAN_LIMITS, PlanTier.FREE, PlanLimits(2, 25, 3, 2, 1, 3600))
    monkeypatch.setattr(tenancy_middleware, "_fallback_counters", {})
    svc, runner = svc_runner
    client = _replica(svc, runner, _DownRedis())
    path = await _webhook_path(svc, client)

    codes = [client.post(path, json={}).status_code for _ in range(3)]

    assert codes == [200, 200, 429]
    assert len(runner.runs) == 2
