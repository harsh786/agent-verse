"""Tests for workflow runs router (run detail, steps, lifecycle control)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.workflow.state import WorkflowRunStatus


def make_app(service: MagicMock) -> TestClient:
    from fastapi import FastAPI, Request

    from app.tenancy.context import PlanLimits, PlanTier, TenantContext
    from app.workflow.router_runs import router

    app = FastAPI()

    class FakeTenant(TenantContext):
        def __init__(self) -> None:
            pass
        tenant_id = "test-tenant"
        plan = PlanTier.FREE
        api_key = "test-key"
        api_key_id = "key-1"
        limits = PlanLimits(60, 25, 3, 2, 1, 3600)

    @app.middleware("http")
    async def inject_state(request: Request, call_next):
        request.app.state.workflow_service = service
        request.app.state.tenant_context = FakeTenant()
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def _run(run_id: str = "run-1", status: str = "complete") -> dict:
    return {
        "run_id": run_id, "workflow_id": "wf-1",
        "workflow_name": "Test WF", "status": status,
        "inputs": {}, "outputs": {"result": "ok"},
        "error": None, "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:01:00Z",
        "duration_ms": 60000, "step_count": 3, "cost_usd": 0.01,
    }


def _step(step_id: str = "step-1") -> dict:
    return {
        "step_id": step_id, "step_type": "tool",
        "status": "complete", "output": {"result": "done"},
        "error": None, "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:30Z", "duration_ms": 30000,
    }


@pytest.fixture
def run_service() -> MagicMock:
    svc = AsyncMock()
    svc.list_runs.return_value = ([_run()], 1)
    svc.get_run.return_value = _run()
    svc.list_step_results.return_value = [_step("step-1"), _step("step-2")]
    svc.get_step_result.return_value = _step("step-1")
    svc.cancel_run.return_value = True
    svc.pause_run.return_value = True
    svc.resume_run.return_value = True
    svc.retry_run.return_value = "run-2"
    svc.get_run_debug.return_value = {"step_outputs": {}, "vars": {}}
    return svc


@pytest.fixture
def client(run_service: MagicMock) -> TestClient:
    return make_app(run_service)


# ── List runs ─────────────────────────────────────────────────────────────────


def test_list_runs(client: TestClient) -> None:
    resp = client.get("/api/v1/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] == 1


def test_list_runs_filter_by_workflow(client: TestClient) -> None:
    resp = client.get("/api/v1/runs?workflow_id=wf-1")
    assert resp.status_code == 200


def test_list_runs_filter_by_status(client: TestClient) -> None:
    resp = client.get("/api/v1/runs?status=complete")
    assert resp.status_code == 200


# ── Get run ───────────────────────────────────────────────────────────────────


def test_get_run(client: TestClient) -> None:
    resp = client.get("/api/v1/runs/run-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-1"
    assert data["status"] == "complete"


def test_get_run_not_found(client: TestClient, run_service: MagicMock) -> None:
    run_service.get_run.return_value = None
    resp = client.get("/api/v1/runs/nonexistent")
    assert resp.status_code == 404


# ── Step results ──────────────────────────────────────────────────────────────


def test_list_step_results(client: TestClient) -> None:
    resp = client.get("/api/v1/runs/run-1/steps")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_get_step_result(client: TestClient) -> None:
    resp = client.get("/api/v1/runs/run-1/steps/step-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["step_id"] == "step-1"
    assert data["status"] == "complete"


def test_get_step_result_not_found(client: TestClient, run_service: MagicMock) -> None:
    run_service.get_step_result.return_value = None
    resp = client.get("/api/v1/runs/run-1/steps/bad-step")
    assert resp.status_code == 404


# ── Lifecycle control ─────────────────────────────────────────────────────────


def test_cancel_run(client: TestClient) -> None:
    resp = client.post("/api/v1/runs/run-1/cancel")
    assert resp.status_code == 202
    data = resp.json()
    assert data["run_id"] == "run-1"
    assert data["status"] == WorkflowRunStatus.CANCELLED.value


def test_cancel_run_not_found(client: TestClient, run_service: MagicMock) -> None:
    run_service.cancel_run.return_value = False
    resp = client.post("/api/v1/runs/run-1/cancel")
    assert resp.status_code == 404


def test_pause_run(client: TestClient) -> None:
    resp = client.post("/api/v1/runs/run-1/pause")
    assert resp.status_code == 202
    assert resp.json()["status"] == WorkflowRunStatus.PAUSED.value


def test_pause_run_invalid_state(client: TestClient, run_service: MagicMock) -> None:
    run_service.pause_run.return_value = False
    resp = client.post("/api/v1/runs/run-1/pause")
    assert resp.status_code == 409


def test_resume_run(client: TestClient) -> None:
    resp = client.post("/api/v1/runs/run-1/resume")
    assert resp.status_code == 202
    assert resp.json()["status"] == WorkflowRunStatus.RUNNING.value


def test_resume_run_not_paused(client: TestClient, run_service: MagicMock) -> None:
    run_service.resume_run.return_value = False
    resp = client.post("/api/v1/runs/run-1/resume")
    assert resp.status_code == 409


def test_retry_run(client: TestClient) -> None:
    resp = client.post("/api/v1/runs/run-1/retry")
    assert resp.status_code == 202
    data = resp.json()
    assert data["run_id"] == "run-2"


def test_retry_run_not_failed(client: TestClient, run_service: MagicMock) -> None:
    run_service.retry_run.return_value = None
    resp = client.post("/api/v1/runs/run-1/retry")
    assert resp.status_code == 409


# ── Debug ─────────────────────────────────────────────────────────────────────


def test_debug_run(client: TestClient) -> None:
    resp = client.get("/api/v1/runs/run-1/debug")
    assert resp.status_code == 200
    data = resp.json()
    assert "step_outputs" in data


def test_debug_run_not_found(client: TestClient, run_service: MagicMock) -> None:
    run_service.get_run_debug.return_value = None
    resp = client.get("/api/v1/runs/bad-run/debug")
    assert resp.status_code == 404


# ── Real WorkflowService backed by an in-memory run store ──────────────────────
# These exercise the actual service methods (not AsyncMock) so the router returns
# real data derived from persisted run/step rows.


class _FakeRunStore:
    """Minimal in-memory WorkflowRunStore double for the real WorkflowService."""

    def __init__(self) -> None:
        self._runs: dict[tuple[str, str], dict] = {}
        self._steps: dict[tuple[str, str], list[dict]] = {}

    async def create(self, *, run_id, workflow_id, tenant_id, trigger_type="api",
                     trigger_payload=None, inputs=None, labels=None, is_test_run=False) -> None:
        self._runs[(tenant_id, run_id)] = {
            "run_id": run_id, "workflow_id": workflow_id, "workflow_name": "WF",
            "status": "pending", "inputs": inputs or {}, "outputs": {}, "error": None,
            "started_at": None, "finished_at": None, "duration_ms": None,
            "step_count": 0, "cost_usd": 0.0,
        }

    async def get(self, tenant_id, run_id):
        return self._runs.get((tenant_id, run_id))

    async def list(self, tenant_id, *, workflow_id=None, status=None, limit=20, offset=0):
        items = [r for (t, _), r in self._runs.items() if t == tenant_id]
        if workflow_id:
            items = [r for r in items if r["workflow_id"] == workflow_id]
        if status:
            items = [r for r in items if r["status"] == status]
        total = len(items)
        return items[offset:offset + limit], total

    async def update_status(self, run_id, status, *, tenant_id, **kw) -> bool:
        run = self._runs.get((tenant_id, run_id))
        if run is None:
            return False
        run["status"] = getattr(status, "value", status)
        return True

    async def list_step_results(self, tenant_id, run_id):
        return self._steps.get((tenant_id, run_id), [])

    async def get_step_result(self, tenant_id, run_id, step_id):
        for s in self._steps.get((tenant_id, run_id), []):
            if s["step_id"] == step_id:
                return s
        return None


@pytest.fixture
def real_client() -> tuple[TestClient, _FakeRunStore]:
    from app.workflow.service import WorkflowService

    store = _FakeRunStore()
    svc = WorkflowService(store=None, run_store=store)
    return make_app(svc), store  # type: ignore[arg-type]


async def _seed(store: _FakeRunStore, tenant: str, run_id: str, status: str) -> None:
    await store.create(run_id=run_id, workflow_id="wf-1", tenant_id=tenant, inputs={"x": 1})
    store._runs[(tenant, run_id)]["status"] = status
    store._steps[(tenant, run_id)] = [
        {"step_id": "s1", "step_type": "tool", "status": "complete",
         "output": {"ok": True}, "error": None, "started_at": None,
         "finished_at": None, "duration_ms": 12.0},
    ]


async def test_real_get_run_returns_persisted_data(
    real_client: tuple[TestClient, _FakeRunStore],
) -> None:
    client, store = real_client
    await _seed(store, "test-tenant", "run-x", "complete")
    resp = client.get("/api/v1/runs/run-x")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-x"
    assert data["status"] == "complete"
    assert data["inputs"] == {"x": 1}


async def test_real_list_and_steps(
    real_client: tuple[TestClient, _FakeRunStore],
) -> None:
    client, store = real_client
    await _seed(store, "test-tenant", "run-x", "running")
    lst = client.get("/api/v1/runs")
    assert lst.status_code == 200 and lst.json()["total"] == 1
    steps = client.get("/api/v1/runs/run-x/steps")
    assert steps.status_code == 200 and steps.json()[0]["step_id"] == "s1"


async def test_real_cancel_and_state_guards(
    real_client: tuple[TestClient, _FakeRunStore],
) -> None:
    client, store = real_client
    await _seed(store, "test-tenant", "run-run", "running")
    # running → cancel succeeds
    assert client.post("/api/v1/runs/run-run/cancel").status_code == 202
    # already cancelled → cancel now 404 (terminal)
    assert client.post("/api/v1/runs/run-run/cancel").status_code == 404
    # resume on a non-paused run → 409
    await _seed(store, "test-tenant", "run-done", "complete")
    assert client.post("/api/v1/runs/run-done/resume").status_code == 409


async def test_real_retry_only_failed(
    real_client: tuple[TestClient, _FakeRunStore],
) -> None:
    client, store = real_client
    await _seed(store, "test-tenant", "run-ok", "complete")
    assert client.post("/api/v1/runs/run-ok/retry").status_code == 409  # not failed
    await _seed(store, "test-tenant", "run-bad", "failed")
    resp = client.post("/api/v1/runs/run-bad/retry")
    assert resp.status_code == 202
    assert resp.json()["run_id"] != "run-bad"  # a fresh run id


async def test_real_debug_run(
    real_client: tuple[TestClient, _FakeRunStore],
) -> None:
    client, store = real_client
    await _seed(store, "test-tenant", "run-x", "complete")
    resp = client.get("/api/v1/runs/run-x/debug")
    assert resp.status_code == 200
    assert "step_outputs" in resp.json()
    assert resp.json()["step_outputs"]["s1"] == {"ok": True}
