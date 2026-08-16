"""Tests for workflow runs router (run detail, steps, lifecycle control)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.workflow.state import WorkflowRunStatus


def make_app(service: MagicMock) -> "TestClient":
    from fastapi import FastAPI, Request
    from app.workflow.router_runs import router
    from app.tenancy.context import TenantContext, PlanTier, PlanLimits

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
def client(run_service: MagicMock) -> "TestClient":
    return make_app(run_service)


# ── List runs ─────────────────────────────────────────────────────────────────


def test_list_runs(client: "TestClient") -> None:
    resp = client.get("/api/v1/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] == 1


def test_list_runs_filter_by_workflow(client: "TestClient") -> None:
    resp = client.get("/api/v1/runs?workflow_id=wf-1")
    assert resp.status_code == 200


def test_list_runs_filter_by_status(client: "TestClient") -> None:
    resp = client.get("/api/v1/runs?status=complete")
    assert resp.status_code == 200


# ── Get run ───────────────────────────────────────────────────────────────────


def test_get_run(client: "TestClient") -> None:
    resp = client.get("/api/v1/runs/run-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-1"
    assert data["status"] == "complete"


def test_get_run_not_found(client: "TestClient", run_service: MagicMock) -> None:
    run_service.get_run.return_value = None
    resp = client.get("/api/v1/runs/nonexistent")
    assert resp.status_code == 404


# ── Step results ──────────────────────────────────────────────────────────────


def test_list_step_results(client: "TestClient") -> None:
    resp = client.get("/api/v1/runs/run-1/steps")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_get_step_result(client: "TestClient") -> None:
    resp = client.get("/api/v1/runs/run-1/steps/step-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["step_id"] == "step-1"
    assert data["status"] == "complete"


def test_get_step_result_not_found(client: "TestClient", run_service: MagicMock) -> None:
    run_service.get_step_result.return_value = None
    resp = client.get("/api/v1/runs/run-1/steps/bad-step")
    assert resp.status_code == 404


# ── Lifecycle control ─────────────────────────────────────────────────────────


def test_cancel_run(client: "TestClient") -> None:
    resp = client.post("/api/v1/runs/run-1/cancel")
    assert resp.status_code == 202
    data = resp.json()
    assert data["run_id"] == "run-1"
    assert data["status"] == WorkflowRunStatus.CANCELLED.value


def test_cancel_run_not_found(client: "TestClient", run_service: MagicMock) -> None:
    run_service.cancel_run.return_value = False
    resp = client.post("/api/v1/runs/run-1/cancel")
    assert resp.status_code == 404


def test_pause_run(client: "TestClient") -> None:
    resp = client.post("/api/v1/runs/run-1/pause")
    assert resp.status_code == 202
    assert resp.json()["status"] == WorkflowRunStatus.PAUSED.value


def test_pause_run_invalid_state(client: "TestClient", run_service: MagicMock) -> None:
    run_service.pause_run.return_value = False
    resp = client.post("/api/v1/runs/run-1/pause")
    assert resp.status_code == 409


def test_resume_run(client: "TestClient") -> None:
    resp = client.post("/api/v1/runs/run-1/resume")
    assert resp.status_code == 202
    assert resp.json()["status"] == WorkflowRunStatus.RUNNING.value


def test_resume_run_not_paused(client: "TestClient", run_service: MagicMock) -> None:
    run_service.resume_run.return_value = False
    resp = client.post("/api/v1/runs/run-1/resume")
    assert resp.status_code == 409


def test_retry_run(client: "TestClient") -> None:
    resp = client.post("/api/v1/runs/run-1/retry")
    assert resp.status_code == 202
    data = resp.json()
    assert data["run_id"] == "run-2"


def test_retry_run_not_failed(client: "TestClient", run_service: MagicMock) -> None:
    run_service.retry_run.return_value = None
    resp = client.post("/api/v1/runs/run-1/retry")
    assert resp.status_code == 409


# ── Debug ─────────────────────────────────────────────────────────────────────


def test_debug_run(client: "TestClient") -> None:
    resp = client.get("/api/v1/runs/run-1/debug")
    assert resp.status_code == 200
    data = resp.json()
    assert "step_outputs" in data


def test_debug_run_not_found(client: "TestClient", run_service: MagicMock) -> None:
    run_service.get_run_debug.return_value = None
    resp = client.get("/api/v1/runs/bad-run/debug")
    assert resp.status_code == 404
