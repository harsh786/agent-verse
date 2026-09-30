"""WF-10: workflow webhook URLs can be revoked, and /wf-hooks dedups sender
retries, rate-limits per workflow and caps the body size.

Old bugs: tokens were a stateless HMAC of (tenant, workflow), so a leaked URL
worked forever; every POST started a new run with no idempotency, no rate limit
and no body cap, so sender retries duplicated runs and a leaked URL could launch
unlimited billed runs.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from app.api.workflows import _WorkflowStore
from app.tenancy.context import PLAN_LIMITS, PlanLimits, PlanTier, TenantContext
from app.workflow.router import router as wf_router
from app.workflow.service import WorkflowService
from app.workflow.webhook_router import MAX_WEBHOOK_BODY_BYTES
from app.workflow.webhook_router import router as hook_router
from app.workflow.webhook_tokens import make_webhook_token, verify_webhook_token_versioned

_T = "tenant-a"


class _TokenRunStore:
    """Token versions (workflow_definitions.trigger_config) + idempotent runs."""

    def __init__(self) -> None:
        self.versions: dict[str, int] = {}

    async def get_webhook_token_version(self, tenant_id: str, workflow_id: str) -> int:
        return self.versions.get(workflow_id, 0)

    async def rotate_webhook_token(self, tenant_id: str, workflow_id: str) -> int:
        self.versions[workflow_id] = self.versions.get(workflow_id, 0) + 1
        return self.versions[workflow_id]


class _Runner:
    """Records runs; an idempotency key replays its first run (like the DB index)."""

    def __init__(self, run_store: Any) -> None:
        self._run_store = run_store
        self.runs: list[dict[str, Any]] = []
        self.by_key: dict[str, str] = {}

    async def _get_plan_tier(self, tenant_id: str) -> str:
        return "free"

    async def run(self, **kw: Any) -> str:
        key = kw.get("idempotency_key")
        if key and key in self.by_key:
            return self.by_key[key]
        run_id = f"run-{len(self.runs) + 1}"
        self.runs.append(kw)
        if key:
            self.by_key[key] = run_id
        return run_id


@pytest.fixture
def env() -> dict[str, Any]:
    run_store = _TokenRunStore()
    svc = WorkflowService(store=_WorkflowStore(), run_store=run_store)
    runner = _Runner(run_store)
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id=_T, plan=PlanTier.FREE, api_key_id="k", roles=("admin",)
        )
        request.app.state.workflow_service = svc
        request.app.state.workflow_runner = runner
        return await call_next(request)

    app.include_router(wf_router, prefix="/api/v1")
    app.include_router(hook_router)
    return {"client": TestClient(app), "svc": svc, "runner": runner, "store": run_store}


async def _published(svc: WorkflowService) -> str:
    wf = await svc.create(
        tenant_id=_T,
        name="hook",
        definition={"name": "hook", "steps": [], "trigger": {"type": "webhook"}},
    )
    wid = str(wf["id"])
    await svc.publish(tenant_id=_T, workflow_id=wid)
    return wid


@pytest.mark.asyncio
async def test_rotated_out_token_is_rejected(env: dict[str, Any]) -> None:
    client, svc = env["client"], env["svc"]
    wid = await _published(svc)
    old = client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"]
    assert client.post(old, json={}).status_code == 200

    rotated = client.post(f"/api/v1/workflows/{wid}/webhook/rotate")
    assert rotated.status_code == 200, rotated.text
    new = rotated.json()["webhook_path"]
    assert new != old
    assert client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"] == new

    revoked = client.post(old, json={})
    assert revoked.status_code == 401
    assert "revoked" in revoked.json()["detail"]
    assert client.post(new, json={}).status_code == 200


def test_versioned_token_round_trip() -> None:
    token = make_webhook_token("t", "wf:with:colons", 3)
    assert verify_webhook_token_versioned(token) == ("t", "wf:with:colons", 3)
    legacy = make_webhook_token("t", "w")
    assert verify_webhook_token_versioned(legacy) == ("t", "w", 0)
    assert verify_webhook_token_versioned(token[:-2] + "xx") is None


@pytest.mark.asyncio
async def test_same_delivery_id_twice_creates_one_run(env: dict[str, Any]) -> None:
    client, svc, runner = env["client"], env["svc"], env["runner"]
    wid = await _published(svc)
    path = client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"]

    first = client.post(path, json={"n": 1}, headers={"X-Delivery-Id": "d-1"})
    retry = client.post(path, json={"n": 1}, headers={"X-Delivery-Id": "d-1"})
    other = client.post(path, json={"n": 2}, headers={"X-Delivery-Id": "d-2"})

    assert first.json()["run_id"] == retry.json()["run_id"]
    assert other.json()["run_id"] != first.json()["run_id"]
    assert len(runner.runs) == 2
    assert runner.runs[0]["idempotency_key"] == "webhook:d-1"


@pytest.mark.asyncio
async def test_exceeding_the_rate_limit_returns_429(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(PLAN_LIMITS, PlanTier.FREE, PlanLimits(2, 25, 3, 2, 1, 3600))
    client, svc, runner = env["client"], env["svc"], env["runner"]
    wid = await _published(svc)
    path = client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"]

    codes = [client.post(path, json={}).status_code for _ in range(3)]

    assert codes == [200, 200, 429]
    assert len(runner.runs) == 2


@pytest.mark.asyncio
async def test_oversized_body_returns_413(env: dict[str, Any]) -> None:
    client, svc, runner = env["client"], env["svc"], env["runner"]
    wid = await _published(svc)
    path = client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"]

    resp = client.post(
        path,
        content=b'{"blob": "' + b"x" * MAX_WEBHOOK_BODY_BYTES + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert runner.runs == []
