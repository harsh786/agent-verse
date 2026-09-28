"""Regression: legacy ``POST /workflows/{id}/run`` uses the durable run path.

Old bug: it executed ``WorkflowExecutor`` synchronously inside the request with
no persistence (random ``uuid4().hex`` run id nobody could query) and on ANY
error silently fell back to submitting a generic goal "Execute workflow <n>" —
a success-shaped response for something that was never the workflow.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.workflows import _WorkflowStore
from app.api.workflows import router as workflows_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.runner import WorkflowRunner

_CTX = TenantContext(tenant_id="tid-wf", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_KEY = "ak_test_legacy_run"
_H = {"X-API-Key": _KEY}


class _RunStore:
    def __init__(self, definition: dict[str, Any] | None) -> None:
        self.definition = definition
        self.runs: dict[str, dict[str, Any]] = {}

    async def create(self, *, run_id: str, workflow_id: str, tenant_id: str, **kw: Any) -> str:
        self.runs[run_id] = {
            "run_id": run_id, "workflow_id": workflow_id, "tenant_id": tenant_id,
            "status": "pending", "inputs": kw.get("inputs") or {},
        }
        return run_id

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    async def get_status(self, tenant_id: str, run_id: str) -> str | None:
        return self.runs[run_id]["status"]

    async def update_status(self, run_id: str, status: Any, *, tenant_id: str, **kw: Any) -> bool:
        self.runs[run_id]["status"] = str(getattr(status, "value", status))
        return True

    async def get_definition(self, workflow_id: str, tenant_id: str) -> dict[str, Any]:
        if self.definition is None:
            raise KeyError(f"workflow definition {workflow_id!r} not found")
        return self.definition

    async def record_step_start(self, **kw: Any) -> str:
        return "x"

    async def record_step_finish(self, **kw: Any) -> bool:
        return True

    async def get_step_result(self, *a: Any) -> None:
        return None


def _app(run_store: _RunStore | None, goal_service: Any = None) -> tuple[TestClient, FastAPI]:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(workflows_router)
    app.state.workflow_store = _WorkflowStore()
    if run_store is not None:
        from app.workflow.compiler import WorkflowCompiler

        app.state.workflow_runner = WorkflowRunner(
            compiler=WorkflowCompiler(ContextResolver(), run_store=run_store),
            run_store=run_store,
        )
    if goal_service is not None:
        app.state.goal_service = goal_service
    return TestClient(app, raise_server_exceptions=False), app


def _defn() -> dict[str, Any]:
    return WorkflowDefinition(
        name="legacy",
        inputs={"who": {"type": "string", "required": True}},
        steps=[StepDefinition(id="a", type="transform", input={"hi": "{{inputs.who}}"})],
    ).model_dump()


def _create(client: TestClient) -> str:
    resp = client.post("/workflows", json={"name": "Legacy"}, headers=_H)
    assert resp.status_code == 201
    return str(resp.json()["id"])


def test_run_creates_persisted_run_and_returns_its_real_id() -> None:
    store = _RunStore(_defn())
    goal_service = AsyncMock()
    client, _ = _app(store, goal_service)
    wf_id = _create(client)
    resp = client.post(f"/workflows/{wf_id}/run", json={"inputs": {"who": "x"}}, headers=_H)
    assert resp.status_code == 202
    body = resp.json()
    assert body["run_id"] in store.runs  # queryable, not a random hex
    assert store.runs[body["run_id"]]["inputs"] == {"who": "x"}
    assert body["status"] == "complete"  # executed inline (no broker in test)
    assert body["run_url"] == f"/api/v1/runs/{body['run_id']}"
    goal_service.submit_goal.assert_not_awaited()


def test_invalid_inputs_are_422_not_a_goal_fallback() -> None:
    store = _RunStore(_defn())
    goal_service = AsyncMock()
    client, _ = _app(store, goal_service)
    wf_id = _create(client)
    resp = client.post(f"/workflows/{wf_id}/run", headers=_H)  # missing required "who"
    assert resp.status_code == 422
    assert store.runs == {}
    goal_service.submit_goal.assert_not_awaited()


def test_unrunnable_definition_is_422() -> None:
    store = _RunStore(None)
    goal_service = AsyncMock()
    client, _ = _app(store, goal_service)
    wf_id = _create(client)
    resp = client.post(f"/workflows/{wf_id}/run", headers=_H)
    assert resp.status_code == 422
    goal_service.submit_goal.assert_not_awaited()


def test_no_durable_engine_is_503_not_fake_dry_run() -> None:
    goal_service = AsyncMock()
    client, _ = _app(None, goal_service)
    wf_id = _create(client)
    resp = client.post(f"/workflows/{wf_id}/run", headers=_H)
    assert resp.status_code == 503
    goal_service.submit_goal.assert_not_awaited()


def test_dry_run_still_validates_without_executing() -> None:
    store = _RunStore(_defn())
    client, _ = _app(store)
    wf_id = _create(client)
    resp = client.post(f"/workflows/{wf_id}/run?dry_run=true", headers=_H)
    assert resp.status_code == 202
    assert resp.json()["status"] == "dry_run"
    assert store.runs == {}
