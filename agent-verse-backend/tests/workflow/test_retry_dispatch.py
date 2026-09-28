"""Regression: retrying a failed run must actually DISPATCH the new run.

Old bug: ``WorkflowService.retry_run`` inserted a fresh ``pending`` row and
returned its id without ever handing it to the runner/Celery, so the retried run
sat ``pending`` forever, while the docstring promised "from the last completed
checkpoint".
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.runner import WorkflowEngineUnavailableError, WorkflowRunner
from app.workflow.service import WorkflowService
from app.workflow.state import StepStatus


class _MemRunStore:
    """In-memory run store implementing the surface the runner/engine use."""

    def __init__(self, definition: dict[str, Any]) -> None:
        self.definition = definition
        self.runs: dict[str, dict[str, Any]] = {}
        self.steps: dict[str, dict[str, dict[str, Any]]] = {}
        self.executed: list[tuple[str, str]] = []

    async def create(self, *, run_id: str, workflow_id: str, tenant_id: str, **kw: Any) -> str:
        self.runs[run_id] = {
            "run_id": run_id,
            "workflow_id": workflow_id,
            "tenant_id": tenant_id,
            "status": "pending",
            "inputs": kw.get("inputs") or {},
            "run_metadata": kw.get("run_metadata") or {},
            "trigger_type": kw.get("trigger_type"),
        }
        return run_id

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    async def get_status(self, tenant_id: str, run_id: str) -> str | None:
        r = self.runs.get(run_id)
        return r["status"] if r else None

    async def update_status(self, run_id: str, status: Any, *, tenant_id: str, **kw: Any) -> bool:
        self.runs[run_id]["status"] = str(getattr(status, "value", status))
        if kw.get("error") is not None:
            self.runs[run_id]["error"] = kw["error"]
        return True

    async def get_workflow_id(self, run_id: str, tenant_id: str | None = None) -> str:
        return self.runs[run_id]["workflow_id"]

    async def get_definition(self, workflow_id: str, tenant_id: str) -> dict[str, Any]:
        return self.definition

    async def record_step_start(self, *, run_id: str, step_id: str, **kw: Any) -> str:
        self.executed.append((run_id, step_id))
        self.steps.setdefault(run_id, {})[step_id] = {"step_id": step_id, "status": "running"}
        return "x"

    async def record_step_finish(
        self, *, run_id: str, step_id: str, status: Any, output: Any = None, **kw: Any
    ) -> bool:
        self.steps[run_id][step_id] = {
            "step_id": step_id,
            "status": str(getattr(status, "value", status)),
            "output": output,
        }
        return True

    async def get_step_result(
        self, tenant_id: str, run_id: str, step_id: str
    ) -> dict[str, Any] | None:
        return self.steps.get(run_id, {}).get(step_id)

    async def copy_completed_step_results(
        self, tenant_id: str, from_run_id: str, to_run_id: str
    ) -> int:
        done = {
            k: dict(v)
            for k, v in self.steps.get(from_run_id, {}).items()
            if v["status"] == StepStatus.COMPLETE.value and v.get("output") is not None
        }
        self.steps.setdefault(to_run_id, {}).update(done)
        return len(done)


def _definition() -> dict[str, Any]:
    return WorkflowDefinition(
        name="retry-me",
        steps=[
            StepDefinition(id="a", type="transform", input={"v": 1}),
            StepDefinition(id="b", type="transform", input={"v": 2}, depends_on=["a"]),
        ],
    ).model_dump()


def _runner(store: _MemRunStore) -> WorkflowRunner:
    from app.workflow.compiler import WorkflowCompiler

    compiler = WorkflowCompiler(ContextResolver(), run_store=store)
    return WorkflowRunner(compiler=compiler, run_store=store)  # no Celery -> inline


@pytest.mark.asyncio
async def test_retry_dispatches_and_skips_completed_steps() -> None:
    store = _MemRunStore(_definition())
    # A failed run where step "a" completed and "b" failed.
    store.runs["old"] = {
        "run_id": "old",
        "workflow_id": "wf-1",
        "tenant_id": "t-1",
        "status": "failed",
        "inputs": {"x": 1},
        "run_metadata": {"callback_url": "https://93.184.216.34/hook"},
    }
    store.steps["old"] = {
        "a": {"step_id": "a", "status": "complete", "output": {"restored": True}},
        "b": {"step_id": "b", "status": "failed", "output": None},
    }
    svc = WorkflowService(store=AsyncMock(), run_store=store)

    new_id = await svc.retry_run("t-1", "old", runner=_runner(store))

    assert new_id and new_id != "old"
    # The retried run was actually executed to a terminal state (not left pending).
    assert store.runs[new_id]["status"] == "complete"
    assert store.runs[new_id]["trigger_type"] == "retry"
    assert store.runs[new_id]["inputs"] == {"x": 1}
    assert store.runs[new_id]["run_metadata"]["retry_of"] == "old"
    # Completed step "a" was NOT re-executed; only "b" ran in the retry.
    assert (new_id, "a") not in store.executed
    assert (new_id, "b") in store.executed


@pytest.mark.asyncio
async def test_retry_rejects_non_failed_run() -> None:
    store = _MemRunStore(_definition())
    store.runs["ok"] = {"run_id": "ok", "workflow_id": "wf-1", "status": "complete"}
    svc = WorkflowService(store=AsyncMock(), run_store=store)
    assert await svc.retry_run("t-1", "ok", runner=_runner(store)) is None


@pytest.mark.asyncio
async def test_retry_without_runner_refuses_instead_of_orphaning_a_row() -> None:
    store = _MemRunStore(_definition())
    store.runs["old"] = {"run_id": "old", "workflow_id": "wf-1", "status": "failed"}
    svc = WorkflowService(store=AsyncMock(), run_store=store)
    with pytest.raises(WorkflowEngineUnavailableError):
        await svc.retry_run("t-1", "old", runner=None)
    assert list(store.runs) == ["old"]  # no orphan pending row created


def test_retry_route_passes_runner_and_503s_without_one() -> None:
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from app.tenancy.context import PlanLimits, PlanTier, TenantContext
    from app.workflow.router_runs import router

    svc = AsyncMock()
    svc.retry_run.side_effect = WorkflowEngineUnavailableError("no store")
    app = FastAPI()

    class _T(TenantContext):
        def __init__(self) -> None:
            pass

        tenant_id = "t-1"
        plan = PlanTier.FREE
        api_key = "k"
        api_key_id = "k1"
        limits = PlanLimits(60, 25, 3, 2, 1, 3600)

    runner_obj = object()

    @app.middleware("http")
    async def _inject(request: Request, call_next: Any) -> Any:
        request.app.state.workflow_service = svc
        request.app.state.workflow_runner = runner_obj
        request.app.state.tenant_context = _T()
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/api/v1/runs/old/retry")
    assert resp.status_code == 503
    assert svc.retry_run.await_args.kwargs["runner"] is runner_obj
