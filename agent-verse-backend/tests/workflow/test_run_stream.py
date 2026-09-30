"""WF-06: GET /runs/{id}/stream is a live stream that never reports a running
or waiting run as failed.

Old bug: a one-shot snapshot that ended with ``run_failed`` for every
non-terminal run, so a UI watching a running or waiting run showed it failed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.workflows import _WorkflowStore
from app.tenancy.context import PlanTier, TenantContext
from app.workflow.router_runs import router
from app.workflow.service import WorkflowService


class _ScriptedRunStore:
    """Each read of the run advances through ``script`` (a list of
    (run_status, step_rows)), like a worker making progress between polls."""

    def __init__(self, script: list[tuple[str, list[dict[str, Any]]]]) -> None:
        self.script = script
        self.reads = 0
        self.current = script[0]

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        if run_id != "run-1":
            return None
        self.current = self.script[min(self.reads, len(self.script) - 1)]
        self.reads += 1
        status, _steps = self.current
        return {
            "run_id": "run-1",
            "workflow_id": "",
            "status": status,
            "outputs": {"ok": True} if status == "complete" else {},
            "error": "boom" if status == "failed" else None,
            "error_step_id": "b" if status == "failed" else None,
        }

    async def list_step_results(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        return self.current[1]


def _step(step_id: str, status: str, started: str = "t0") -> dict[str, Any]:
    return {"step_id": step_id, "step_type": "transform", "status": status, "started_at": started}


async def _collect(store: _ScriptedRunStore, **kw: Any) -> list[dict[str, Any]]:
    svc = WorkflowService(store=_WorkflowStore(), run_store=store)
    return [
        e
        async for e in svc.stream_run_events(
            "t-1", "run-1", poll_interval=0, heartbeat_interval=3600, **kw
        )
    ]


@pytest.mark.asyncio
async def test_waiting_run_streams_run_waiting_then_completes() -> None:
    store = _ScriptedRunStore(
        [
            ("running", [_step("a", "running")]),
            ("waiting_hitl", [_step("a", "complete"), _step("gate", "waiting_hitl")]),
            ("waiting_hitl", [_step("a", "complete"), _step("gate", "waiting_hitl")]),
            ("running", [_step("a", "complete"), _step("gate", "complete")]),
            ("complete", [_step("a", "complete"), _step("gate", "complete")]),
        ]
    )
    events = await _collect(store)
    names = [e["event"] for e in events]

    assert "run_failed" not in names
    assert names[-1] == "run_completed"
    assert events[-1]["outputs"] == {"ok": True}
    assert names.index("run_waiting") < names.index("run_completed")
    assert [e["status"] for e in events if e["event"] == "run_status"] == [
        "running",
        "waiting_hitl",
        "running",
        "complete",
    ]
    # Each step transition is reported once.
    assert [(e["step_id"], e["event"]) for e in events if e.get("step_id")] == [
        ("a", "step_started"),
        ("a", "step_completed"),
        ("gate", "step_status"),
        ("gate", "step_completed"),
    ]


@pytest.mark.asyncio
async def test_failed_and_cancelled_runs_end_with_their_own_event() -> None:
    failed = await _collect(_ScriptedRunStore([("running", []), ("failed", [])]))
    assert failed[-1] == {
        "event": "run_failed",
        "status": "failed",
        "error": "boom",
        "error_step_id": "b",
    }
    cancelled = await _collect(_ScriptedRunStore([("cancelled", [])]))
    assert cancelled[-1]["event"] == "run_cancelled"


@pytest.mark.asyncio
async def test_long_waits_heartbeat_and_time_out_without_failing() -> None:
    store = _ScriptedRunStore([("waiting_hitl", [])])
    svc = WorkflowService(store=_WorkflowStore(), run_store=store)
    events = [
        e
        async for e in svc.stream_run_events(
            "t-1", "run-1", poll_interval=0.01, heartbeat_interval=0, max_duration=0.05
        )
    ]
    names = [e["event"] for e in events]
    assert "heartbeat" in names
    assert names[-1] == "stream_timeout"
    assert "run_failed" not in names


def test_http_stream_for_a_waiting_run_is_not_reported_failed() -> None:
    store = _ScriptedRunStore(
        [("waiting_hitl", []), ("waiting_hitl", []), ("complete", [_step("a", "complete")])]
    )
    svc = WorkflowService(store=_WorkflowStore(), run_store=store)
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = TenantContext(tenant_id="t-1", plan=PlanTier.FREE, api_key_id="k")
        request.app.state.workflow_service = svc
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    import app.workflow.service as service_mod

    original = service_mod.WorkflowService.stream_run_events

    def fast(self: Any, tenant_id: str, run_id: str, **kw: Any) -> Any:
        return original(self, tenant_id, run_id, poll_interval=0, heartbeat_interval=3600)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(service_mod.WorkflowService, "stream_run_events", fast)
        with TestClient(app).stream("GET", "/api/v1/runs/run-1/stream") as resp:
            assert resp.status_code == 200
            frames = [ln[6:] for ln in resp.iter_lines() if ln.startswith("data: ")]

    assert frames[-1] == "[DONE]"
    events = [json.loads(f) for f in frames[:-1]]
    names = [e["event"] for e in events]
    assert names[:2] == ["run_status", "run_waiting"]
    assert names[-1] == "run_completed"
    assert "run_failed" not in names
