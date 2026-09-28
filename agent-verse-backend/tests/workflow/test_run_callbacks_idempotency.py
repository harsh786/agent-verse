"""Regression: run-completion callbacks are delivered and idempotency keys enforced.

Old bugs:
  * ``WorkflowRunner.send_callback`` was never called; a trigger's
    ``callback_url`` only landed in never-persisted ``run_metadata`` — no caller
    was ever notified.
  * ``idempotency_key`` was accepted and ignored, so a retried trigger started a
    second run.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from app.workflow import callbacks
from app.workflow.context import ContextResolver
from app.workflow.dsl import CallbackConfig, StepDefinition, WorkflowDefinition
from app.workflow.runner import (
    WorkflowEngineUnavailableError,
    WorkflowRunner,
    WorkflowValidationError,
)
from app.workflow.state import WorkflowRunStatus

PUBLIC_URL = "https://93.184.216.34/hook"  # literal public IP: no DNS needed


class _Store:
    """In-memory store honouring (tenant, workflow, idempotency_key) uniqueness."""

    def __init__(self, definition: dict[str, Any]) -> None:
        self.definition = definition
        self.runs: dict[str, dict[str, Any]] = {}
        self.keys: dict[tuple[str, str, str], str] = {}
        self.step_starts = 0

    async def create(self, *, run_id: str, workflow_id: str, tenant_id: str, **kw: Any) -> str:
        key = kw.get("idempotency_key")
        if key and (tenant_id, workflow_id, key) in self.keys:
            return self.keys[(tenant_id, workflow_id, key)]
        if key:
            self.keys[(tenant_id, workflow_id, key)] = run_id
        self.runs[run_id] = {
            "run_id": run_id,
            "workflow_id": workflow_id,
            "status": "pending",
            "inputs": kw.get("inputs") or {},
            "run_metadata": kw.get("run_metadata") or {},
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
        return self.definition

    async def record_step_start(self, **kw: Any) -> str:
        self.step_starts += 1
        return "x"

    async def record_step_finish(self, **kw: Any) -> bool:
        return True

    async def get_step_result(self, *a: Any) -> None:
        return None


def _defn(**kw: Any) -> dict[str, Any]:
    return WorkflowDefinition(
        name="cb", steps=[StepDefinition(id="a", type="transform", input={"v": 1})], **kw
    ).model_dump()


def _runner(store: _Store, celery: Any = None) -> WorkflowRunner:
    from app.workflow.compiler import WorkflowCompiler

    return WorkflowRunner(
        compiler=WorkflowCompiler(ContextResolver(), run_store=store),
        run_store=store,
        celery_app=celery,
    )


async def _drain() -> None:
    for _ in range(5):
        await asyncio.sleep(0)
    if callbacks._INFLIGHT:
        await asyncio.gather(*list(callbacks._INFLIGHT))


# ── Idempotency ───────────────────────────────────────────────────────────────


async def test_same_idempotency_key_returns_existing_run_and_runs_once() -> None:
    store = _Store(_defn())
    runner = _runner(store)
    first = await runner.run("wf-1", "t-1", {}, idempotency_key="k-1", wait_for_completion=True)
    second = await runner.run("wf-1", "t-1", {}, idempotency_key="k-1", wait_for_completion=True)
    assert first == second
    assert len(store.runs) == 1
    assert store.step_starts == 1  # the duplicate did not execute again
    other = await runner.run("wf-1", "t-1", {}, idempotency_key="k-2", wait_for_completion=True)
    assert other != first


async def test_idempotency_key_without_persistent_store_is_refused() -> None:
    runner = WorkflowRunner(compiler=MagicMock())
    with pytest.raises(WorkflowEngineUnavailableError):
        await runner.run("wf-1", "t-1", {}, idempotency_key="k")


async def test_trigger_passes_idempotency_key_through() -> None:
    store = _Store(_defn())
    runner = _runner(store)
    a = await runner.trigger(workflow_id="wf-1", tenant_id="t-1", inputs={}, idempotency_key="z")
    b = await runner.trigger(workflow_id="wf-1", tenant_id="t-1", inputs={}, idempotency_key="z")
    assert a["run_id"] == b["run_id"]


# ── Callback URL validation ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/cb", "http://169.254.169.254/latest", "ftp://93.184.216.34/x", "http://10.0.0.5/"],
)
async def test_private_or_bad_callback_url_rejected_at_trigger(url: str) -> None:
    store = _Store(_defn())
    with pytest.raises(WorkflowValidationError):
        await _runner(store).trigger(
            workflow_id="wf-1", tenant_id="t-1", inputs={}, callback_url=url
        )
    assert store.runs == {}  # rejected before any run row was created


# ── Delivery on terminal status ───────────────────────────────────────────────


async def test_inline_completion_posts_signed_callback() -> None:
    store = _Store(_defn())
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(PUBLIC_URL).mock(return_value=httpx.Response(200))
        run_id = await _runner(store).run(
            "wf-1",
            "t-1",
            {},
            run_metadata={"callback_url": PUBLIC_URL},
            wait_for_completion=True,
        )
        await _drain()
    assert route.call_count == 1
    req = route.calls[0].request
    body = json.loads(req.content)
    assert body["run_id"] == run_id
    assert body["status"] == "complete"
    assert body["event"] == "workflow.run.complete"
    # Signature verifies with the per-(tenant, workflow) derived key.
    from app.workflow.webhook_tokens import callback_signing_secret

    ts = req.headers["X-AgentVerse-Timestamp"]
    expected = hmac.new(
        callback_signing_secret("t-1", "wf-1").encode(),
        ts.encode() + b"." + req.content,
        hashlib.sha256,
    ).hexdigest()
    assert req.headers["X-AgentVerse-Signature"] == f"sha256={expected}"


async def test_worker_execute_fresh_reads_callback_from_persisted_metadata() -> None:
    """The worker rebuilds state from the DB row; the callback_url must survive."""
    store = _Store(_defn())
    await store.create(
        run_id="r-9", workflow_id="wf-1", tenant_id="t-1",
        run_metadata={"callback_url": PUBLIC_URL},
    )
    with respx.mock() as mock:
        route = mock.post(PUBLIC_URL).mock(return_value=httpx.Response(204))
        await _runner(store).execute_fresh("r-9", "wf-1", "t-1")
        await _drain()
    assert store.runs["r-9"]["status"] == "complete"
    assert route.call_count == 1


async def test_failed_run_callback_and_dsl_on_failure_flag() -> None:
    # DSL callback with on_failure=False: a failed run must NOT call out.
    store = _Store(_defn(callback=CallbackConfig(url=PUBLIC_URL, on_failure=False)))
    runner = _runner(store)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(PUBLIC_URL).mock(return_value=httpx.Response(200))
        runner._fire_callback(
            WorkflowRunStatus.FAILED,
            {"run_id": "r", "workflow_id": "wf-1", "tenant_id": "t-1"},
            WorkflowDefinition.from_json(store.definition),
        )
        await _drain()
        assert route.call_count == 0
        runner._fire_callback(
            WorkflowRunStatus.COMPLETE,
            {"run_id": "r", "workflow_id": "wf-1", "tenant_id": "t-1"},
            WorkflowDefinition.from_json(store.definition),
        )
        await _drain()
        assert route.call_count == 1


async def test_non_terminal_and_test_runs_never_call_out() -> None:
    runner = _runner(_Store(_defn()))
    with patch.object(callbacks, "dispatch_callback") as dispatch:
        state = {"run_id": "r", "run_metadata": {"callback_url": PUBLIC_URL}}
        runner._fire_callback(WorkflowRunStatus.WAITING_HITL, state, None)
        runner._fire_callback(WorkflowRunStatus.PAUSED, state, None)
        runner._fire_callback(WorkflowRunStatus.COMPLETE, {**state, "is_test_run": True}, None)
    dispatch.assert_not_called()


async def test_celery_path_enqueues_delivery_task_instead_of_posting_inline() -> None:
    runner = _runner(_Store(_defn()), celery=MagicMock())
    with patch("app.workflow.celery_tasks.deliver_workflow_callback") as task:
        runner._fire_callback(
            WorkflowRunStatus.COMPLETE,
            {"run_id": "r", "workflow_id": "wf-1", "tenant_id": "t-1",
             "run_metadata": {"callback_url": PUBLIC_URL}},
            None,
        )
    task.apply_async.assert_called_once()
    args = task.apply_async.call_args.kwargs["args"]
    assert args[0] == PUBLIC_URL and args[2] == "t-1" and args[3] == "wf-1"


async def test_callback_failure_never_fails_the_run() -> None:
    store = _Store(_defn())
    with patch.object(callbacks, "dispatch_callback", side_effect=RuntimeError("boom")):
        run_id = await _runner(store).run(
            "wf-1", "t-1", {}, run_metadata={"callback_url": PUBLIC_URL},
            wait_for_completion=True,
        )
    assert store.runs[run_id]["status"] == "complete"


# ── Delivery semantics ────────────────────────────────────────────────────────


async def test_delivery_retries_transient_then_succeeds() -> None:
    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    with respx.mock() as mock:
        route = mock.post(PUBLIC_URL).mock(
            side_effect=[httpx.Response(503), httpx.ConnectError("x"), httpx.Response(200)]
        )
        ok = await callbacks.deliver_with_retries(
            PUBLIC_URL, {"run_id": "r", "status": "complete"},
            tenant_id="t", workflow_id="w", sleep=_sleep,
        )
    assert ok is True
    assert route.call_count == 3
    assert sleeps == [callbacks.backoff_seconds(1), callbacks.backoff_seconds(2)]


async def test_delivery_is_bounded_and_permanent_4xx_not_retried() -> None:
    async def _sleep(s: float) -> None:
        return None

    with respx.mock() as mock:
        route = mock.post(PUBLIC_URL).mock(return_value=httpx.Response(500))
        ok = await callbacks.deliver_with_retries(
            PUBLIC_URL, {"run_id": "r"}, tenant_id="t", workflow_id="w",
            max_attempts=3, sleep=_sleep,
        )
    assert ok is False and route.call_count == 3

    with respx.mock() as mock:
        route = mock.post(PUBLIC_URL).mock(return_value=httpx.Response(400))
        ok = await callbacks.deliver_with_retries(
            PUBLIC_URL, {"run_id": "r"}, tenant_id="t", workflow_id="w", sleep=_sleep
        )
    assert ok is False and route.call_count == 1


async def test_delivery_rechecks_ssrf_and_never_follows_redirects() -> None:
    with pytest.raises(callbacks.CallbackPermanentError):
        await callbacks.deliver_callback(
            "http://127.0.0.1:8000/x", {"run_id": "r"}, tenant_id="t", workflow_id="w"
        )
    with respx.mock(assert_all_called=False) as mock:
        mock.post(PUBLIC_URL).mock(
            return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
        )
        internal = mock.get("http://169.254.169.254/").mock(return_value=httpx.Response(200))
        with pytest.raises(callbacks.CallbackPermanentError):
            await callbacks.deliver_callback(
                PUBLIC_URL, {"run_id": "r"}, tenant_id="t", workflow_id="w"
            )
    assert internal.call_count == 0


def test_trigger_route_maps_bad_callback_to_422() -> None:
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from app.tenancy.context import PlanLimits, PlanTier, TenantContext
    from app.workflow.router import router

    class _T(TenantContext):
        def __init__(self) -> None:
            pass

        tenant_id = "t-1"
        plan = PlanTier.FREE
        api_key = "k"
        api_key_id = "k1"
        limits = PlanLimits(60, 25, 3, 2, 1, 3600)

    app = FastAPI()
    runner = _runner(_Store(_defn()))

    @app.middleware("http")
    async def _inject(request: Request, call_next: Any) -> Any:
        request.app.state.workflow_runner = runner
        request.app.state.tenant_context = _T()
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/workflows/wf-1/trigger",
        json={"inputs": {}, "callback_url": "http://127.0.0.1/steal"},
    )
    assert resp.status_code == 422
