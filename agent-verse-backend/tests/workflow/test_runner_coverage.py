"""Coverage-focused tests for WorkflowRunner beyond the trigger/validation
paths already covered by ``test_runner.py`` / ``test_runner_trigger.py``.

Targets real scenarios in the currently-uncovered branches of
``app/workflow/runner.py``:

  * ``run()``: trigger_transform application, Celery dispatch (production path)
  * ``resume()``: no-op when the run/workflow is unknown, in-process vs Celery
    re-dispatch
  * ``execute_fresh()``: the out-of-process worker entrypoint — success,
    WorkflowCancelled, WorkflowPaused, and generic-exception branches, plus
    the RUNNING status stamp and its is_test_run / no-run-store skips
  * ``execute_resume_fresh()``: HITL resume success and failure branches
  * ``_finalize_status()``: no-op when there is no run store
  * ``resume_from_hitl()``: Celery re-dispatch branch
  * ``_load_definition()``: in-memory fallback (no run store) and stamping
    ``definition.id`` when the stored DSL carries none
  * ``_get_plan_tier()``: tenant_service lookup failure falls back to "free"
  * ``send_callback()``: skip-if-unconfigured, success POST, skip-on-failure
    unless ``on_failure``, and swallowing a POST error
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.workflow.dsl import CallbackConfig, WorkflowDefinition
from app.workflow.runner import WorkflowRunner
from app.workflow.state import WorkflowCancelled, WorkflowPaused, WorkflowRunStatus

pytestmark = pytest.mark.asyncio


class _FakeCompiled:
    """A compiled-graph double whose ``ainvoke`` is scripted per test."""

    def __init__(self, result: Any = None, exc: Exception | None = None) -> None:
        self._result = result
        self._exc = exc
        self.invoked_with: tuple[Any, Any] | None = None

    async def ainvoke(self, state: dict[str, Any], config: dict[str, Any]) -> Any:
        self.invoked_with = (state, config)
        if self._exc is not None:
            raise self._exc
        return self._result


class _FakeCompiler:
    def __init__(self, compiled: _FakeCompiled) -> None:
        self._compiled = compiled
        self.compiled_with: WorkflowDefinition | None = None

    def compile(self, definition: WorkflowDefinition) -> _FakeCompiled:
        self.compiled_with = definition
        return self._compiled


class _FakeRunStore:
    def __init__(self, definition: WorkflowDefinition, record: dict[str, Any] | None = None) -> None:
        self._definition = definition
        self._record = record or {"inputs": {}, "labels": {}}
        self.status_calls: list[dict[str, Any]] = []
        self.created: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> None:
        self.created = kwargs

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        return self._record

    async def get_workflow_id(self, run_id: str, tenant_id: str) -> str:
        return self._definition.id or ""

    async def get_definition(self, workflow_id: str, tenant_id: str) -> dict[str, Any]:
        return self._definition.to_json()

    async def update_status(self, run_id: str, status: Any, *, tenant_id: str, **kwargs: Any) -> bool:
        self.status_calls.append({"run_id": run_id, "status": status, "tenant_id": tenant_id, **kwargs})
        return True


def _wf(**kwargs: Any) -> WorkflowDefinition:
    return WorkflowDefinition(id="wf-1", name="wf", **kwargs)


# ── trigger(): real run_store round trip ─────────────────────────────────────


async def test_trigger_returns_persisted_record_from_real_run() -> None:
    """Exercise the real trigger() -> run() -> run_store.get() path (not a
    mocked ``runner.run``), so the run_store lookup + fallback dict shaping
    in trigger() itself is covered, not just its delegation contract."""
    definition = _wf()
    run_store = _FakeRunStore(definition, record=None)
    run_store._record = {"run_id": "placeholder", "status": "complete"}
    compiled = _FakeCompiled(result={"status": WorkflowRunStatus.COMPLETE})
    runner = WorkflowRunner(compiler=_FakeCompiler(compiled), run_store=run_store)

    out = await runner.trigger(
        workflow_id="wf-1", tenant_id="t-1", inputs={}, dry_run=True
    )

    assert out["status"] == "complete"
    assert run_store.created is not None


async def test_trigger_falls_back_to_minimal_dict_when_no_record() -> None:
    definition = _wf()
    run_store = _FakeRunStore(definition)
    run_store._record = None  # get() returns None -> trigger() must fall back
    compiled = _FakeCompiled(result={"status": WorkflowRunStatus.COMPLETE})
    runner = WorkflowRunner(compiler=_FakeCompiler(compiled), run_store=run_store)

    out = await runner.trigger(workflow_id="wf-1", tenant_id="t-1", inputs={})

    assert out["workflow_id"] == "wf-1"
    assert out["status"] == "pending"


# ── run(): trigger_transform application ──────────────────────────────────────


async def test_run_applies_trigger_transform_before_validation() -> None:
    definition = _wf(trigger_transform={"env": "{{ trigger.selected_env }}"})
    run_store = _FakeRunStore(definition)
    compiled = _FakeCompiled(result={"status": WorkflowRunStatus.COMPLETE})
    compiler = _FakeCompiler(compiled)
    runner = WorkflowRunner(compiler=compiler, run_store=run_store)

    await runner.run(
        workflow_id="wf-1",
        tenant_id="t-1",
        inputs={"a": 1},
        trigger_payload={"selected_env": "prod"},
        is_test_run=True,
    )

    seeded_state = compiled.invoked_with[0]
    assert seeded_state["inputs"]["env"] == "prod"
    assert seeded_state["inputs"]["a"] == 1


# ── run(): production Celery dispatch branch ──────────────────────────────────


async def test_run_dispatches_to_celery_when_not_inline() -> None:
    definition = _wf()
    run_store = _FakeRunStore(definition)
    compiler = _FakeCompiler(_FakeCompiled())
    tenant_service = AsyncMock()
    tenant_service.get = AsyncMock(return_value=MagicMock(plan="professional"))
    runner = WorkflowRunner(
        compiler=compiler,
        run_store=run_store,
        celery_app=MagicMock(),
        tenant_service=tenant_service,
    )

    with patch("app.workflow.celery_tasks.execute_workflow_run") as mocked_task:
        run_id = await runner.run(
            workflow_id="wf-1",
            tenant_id="t-1",
            inputs={},
            is_test_run=False,
            wait_for_completion=False,
        )

    mocked_task.apply_async.assert_called_once()
    call = mocked_task.apply_async.call_args
    assert call.kwargs["args"] == [run_id, "wf-1", "t-1"]
    assert call.kwargs["queue"] == "workflows.professional"
    # Inline execution must NOT have happened.
    assert compiler.compiled_with is None


# ── resume() ────────────────────────────────────────────────────────────────


async def test_resume_noop_when_workflow_unknown() -> None:
    run_store = AsyncMock()
    run_store.get_workflow_id = AsyncMock(return_value="")
    compiler = _FakeCompiler(_FakeCompiled())
    runner = WorkflowRunner(compiler=compiler, run_store=run_store)

    await runner.resume(run_id="ghost-run", tenant_id="t-1")

    assert compiler.compiled_with is None


async def test_resume_no_run_store_noop() -> None:
    compiler = _FakeCompiler(_FakeCompiled())
    runner = WorkflowRunner(compiler=compiler, run_store=None)

    # workflow_id resolves to "" when there is no run store -> early return.
    await runner.resume(run_id="run-1", tenant_id="t-1")

    assert compiler.compiled_with is None


async def test_resume_inline_when_no_celery() -> None:
    definition = _wf()
    run_store = _FakeRunStore(definition)
    compiled = _FakeCompiled(result={"status": WorkflowRunStatus.COMPLETE})
    compiler = _FakeCompiler(compiled)
    runner = WorkflowRunner(compiler=compiler, run_store=run_store, celery_app=None)

    await runner.resume(run_id="run-1", tenant_id="t-1")

    # _load_definition round-trips through JSON, so it won't be the same
    # object — but it must carry the same id and have actually been compiled.
    assert compiler.compiled_with is not None
    assert compiler.compiled_with.id == definition.id
    assert run_store.status_calls[-1]["status"] == WorkflowRunStatus.COMPLETE


async def test_resume_dispatches_celery_when_configured() -> None:
    definition = _wf()
    run_store = _FakeRunStore(definition)
    compiler = _FakeCompiler(_FakeCompiled())
    tenant_service = AsyncMock()
    tenant_service.get = AsyncMock(return_value=MagicMock(plan="starter"))
    runner = WorkflowRunner(
        compiler=compiler,
        run_store=run_store,
        celery_app=MagicMock(),
        tenant_service=tenant_service,
    )

    with patch("app.workflow.celery_tasks.execute_workflow_run") as mocked_task:
        await runner.resume(run_id="run-1", tenant_id="t-1")

    mocked_task.apply_async.assert_called_once()
    assert mocked_task.apply_async.call_args.kwargs["queue"] == "workflows.starter"
    assert compiler.compiled_with is None  # not executed in-process


# ── execute_fresh() ─────────────────────────────────────────────────────────


async def test_execute_fresh_success_stamps_running_then_finalizes() -> None:
    definition = _wf()
    run_store = _FakeRunStore(definition, record={"inputs": {"x": 1}, "labels": {"k": "v"}})
    compiled = _FakeCompiled(result={"status": WorkflowRunStatus.COMPLETE, "outputs": {"y": 2}})
    compiler = _FakeCompiler(compiled)
    runner = WorkflowRunner(compiler=compiler, run_store=run_store)

    await runner.execute_fresh("run-1", "wf-1", "t-1")

    statuses = [c["status"] for c in run_store.status_calls]
    assert statuses == [WorkflowRunStatus.RUNNING, WorkflowRunStatus.COMPLETE]
    seeded_state = compiled.invoked_with[0]
    assert seeded_state["inputs"] == {"x": 1}
    assert seeded_state["labels"]["k"] == "v"


async def test_execute_fresh_skips_running_stamp_for_test_run() -> None:
    definition = _wf()
    run_store = _FakeRunStore(definition)
    compiled = _FakeCompiled(result={"status": WorkflowRunStatus.COMPLETE})
    runner = WorkflowRunner(compiler=_FakeCompiler(compiled), run_store=run_store)

    await runner.execute_fresh("run-1", "wf-1", "t-1", is_test_run=True)

    statuses = [c["status"] for c in run_store.status_calls]
    assert statuses == [WorkflowRunStatus.COMPLETE]


async def test_execute_fresh_no_run_store_skips_stamp_but_still_invokes() -> None:
    definition = _wf()
    compiled = _FakeCompiled(result={"status": WorkflowRunStatus.COMPLETE})
    runner = WorkflowRunner(compiler=_FakeCompiler(compiled), run_store=None)

    # Must not raise despite there being no run store to persist against.
    await runner.execute_fresh("run-1", "wf-1", "t-1")
    assert compiled.invoked_with is not None


async def test_execute_fresh_cancelled_marks_cancelled_not_failed() -> None:
    definition = _wf()
    run_store = _FakeRunStore(definition)
    compiled = _FakeCompiled(exc=WorkflowCancelled())
    runner = WorkflowRunner(compiler=_FakeCompiler(compiled), run_store=run_store)

    await runner.execute_fresh("run-1", "wf-1", "t-1")

    statuses = [c["status"] for c in run_store.status_calls]
    assert statuses[-1] == WorkflowRunStatus.CANCELLED
    assert WorkflowRunStatus.FAILED not in statuses


async def test_execute_fresh_paused_leaves_status_untouched() -> None:
    definition = _wf()
    run_store = _FakeRunStore(definition)
    compiled = _FakeCompiled(exc=WorkflowPaused())
    runner = WorkflowRunner(compiler=_FakeCompiler(compiled), run_store=run_store)

    await runner.execute_fresh("run-1", "wf-1", "t-1")

    # Only the initial RUNNING stamp — no terminal status written by the runner
    # (the API already flipped it to PAUSED before this call).
    statuses = [c["status"] for c in run_store.status_calls]
    assert statuses == [WorkflowRunStatus.RUNNING]


async def test_execute_fresh_generic_exception_marks_failed_with_error() -> None:
    definition = _wf()
    run_store = _FakeRunStore(definition)
    compiled = _FakeCompiled(exc=RuntimeError("tool exploded"))
    runner = WorkflowRunner(compiler=_FakeCompiler(compiled), run_store=run_store)

    await runner.execute_fresh("run-1", "wf-1", "t-1")

    last = run_store.status_calls[-1]
    assert last["status"] == WorkflowRunStatus.FAILED
    assert "tool exploded" in last["error"]


# ── execute_resume_fresh() ──────────────────────────────────────────────────


async def test_execute_resume_fresh_seeds_hitl_decision_and_finalizes() -> None:
    definition = _wf()
    run_store = _FakeRunStore(definition, record={"inputs": {}, "labels": {}})
    compiled = _FakeCompiled(result={"status": WorkflowRunStatus.COMPLETE, "outputs": {}})
    runner = WorkflowRunner(compiler=_FakeCompiler(compiled), run_store=run_store)

    await runner.execute_resume_fresh(
        "run-1",
        "wf-1",
        "t-1",
        step_id="gate",
        action="approve",
        actor_id="reviewer-1",
        note="looks good",
        form_data={"f": 1},
    )

    seeded_state = compiled.invoked_with[0]
    assert seeded_state["hitl_request_id"] == "gate"
    assert seeded_state["hitl_action"] == "approve"
    assert seeded_state["hitl_reviewer"] == "reviewer-1"
    assert seeded_state["hitl_note"] == "looks good"
    assert seeded_state["status"] == WorkflowRunStatus.RUNNING
    assert run_store.status_calls[-1]["status"] == WorkflowRunStatus.COMPLETE
    # Uses an isolated checkpoint thread so it can't collide with a stale one.
    assert compiled.invoked_with[1]["configurable"]["thread_id"] == "run-1::resume"


async def test_execute_resume_fresh_failure_marks_failed() -> None:
    definition = _wf()
    run_store = _FakeRunStore(definition)
    compiled = _FakeCompiled(exc=RuntimeError("resume blew up"))
    runner = WorkflowRunner(compiler=_FakeCompiler(compiled), run_store=run_store)

    await runner.execute_resume_fresh(
        "run-1", "wf-1", "t-1", step_id="gate", action="approve", actor_id="r1"
    )

    last = run_store.status_calls[-1]
    assert last["status"] == WorkflowRunStatus.FAILED
    assert "resume blew up" in last["error"]


# ── _finalize_status() ───────────────────────────────────────────────────────


async def test_finalize_status_noop_without_run_store() -> None:
    runner = WorkflowRunner(compiler=MagicMock(), run_store=None)
    # Must not raise even though there is nothing to persist to.
    await runner._finalize_status("run-1", "t-1", {"status": WorkflowRunStatus.COMPLETE})


# ── resume_from_hitl(): Celery re-dispatch branch ────────────────────────────


async def test_resume_from_hitl_dispatches_via_celery() -> None:
    definition = _wf()
    run_store = _FakeRunStore(definition)
    tenant_service = AsyncMock()
    tenant_service.get = AsyncMock(return_value=MagicMock(plan="enterprise"))
    runner = WorkflowRunner(
        compiler=_FakeCompiler(_FakeCompiled()),
        run_store=run_store,
        celery_app=MagicMock(),
        tenant_service=tenant_service,
    )

    with patch("app.workflow.celery_tasks.execute_workflow_run") as mocked_task:
        await runner.resume_from_hitl(
            run_id="run-1",
            step_id="gate",
            action="approve",
            actor_id="reviewer-9",
            note="ok",
            form_data=None,
            tenant_id="t-1",
        )

    mocked_task.apply_async.assert_called_once()
    call = mocked_task.apply_async.call_args
    assert call.kwargs["args"] == ["run-1", "wf-1", "t-1"]
    assert call.kwargs["kwargs"]["resume"] is True
    assert call.kwargs["kwargs"]["hitl_decision"]["step_id"] == "gate"
    assert call.kwargs["queue"] == "workflows.enterprise"


# ── _load_definition() ────────────────────────────────────────────────────────


async def test_load_definition_fallback_without_run_store() -> None:
    runner = WorkflowRunner(compiler=MagicMock(), run_store=None)
    definition = await runner._load_definition("wf-fallback", "t-1")
    assert definition.id == "wf-fallback"
    assert definition.name == "test"


async def test_load_definition_stamps_id_when_dsl_carries_none() -> None:
    stored = WorkflowDefinition(name="no-id-in-dsl")  # id defaults to ""
    run_store = AsyncMock()
    run_store.get_definition = AsyncMock(return_value=stored.to_json())
    runner = WorkflowRunner(compiler=MagicMock(), run_store=run_store)

    definition = await runner._load_definition("wf-real-id", "t-1")

    assert definition.id == "wf-real-id"


# ── _get_plan_tier(): tenant lookup failure ──────────────────────────────────


async def test_get_plan_tier_falls_back_to_free_on_lookup_error() -> None:
    tenant_service = AsyncMock()
    tenant_service.get = AsyncMock(side_effect=RuntimeError("db down"))
    runner = WorkflowRunner(compiler=MagicMock(), tenant_service=tenant_service)

    tier = await runner._get_plan_tier("t-1")

    assert tier == "free"


# ── send_callback() ──────────────────────────────────────────────────────────


async def test_send_callback_skips_when_not_configured() -> None:
    runner = WorkflowRunner(compiler=MagicMock())
    definition = _wf(callback=None)
    # Must not raise / attempt any network call.
    await runner.send_callback(definition, {"run_id": "r1"})


async def test_send_callback_skips_on_failure_unless_on_failure_flag() -> None:
    runner = WorkflowRunner(compiler=MagicMock())
    definition = _wf(callback=CallbackConfig(url="https://cb.example.com/hook", on_failure=False))
    state = {"run_id": "r1", "status": WorkflowRunStatus.FAILED}

    posted = AsyncMock()

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def post(self, *args: Any, **kwargs: Any) -> None:
            posted(*args, **kwargs)

    with patch.object(httpx, "AsyncClient", lambda **kw: _Client()):
        await runner.send_callback(definition, state)

    posted.assert_not_called()


async def test_send_callback_posts_final_outputs_on_success() -> None:
    runner = WorkflowRunner(compiler=MagicMock())
    definition = _wf(callback=CallbackConfig(url="https://cb.example.com/hook", on_failure=True))
    state = {
        "run_id": "r1",
        "status": WorkflowRunStatus.COMPLETE,
        "outputs": {"answer": 42},
        "labels": {"env": "prod"},
        "cost_usd": 0.05,
    }

    posted = AsyncMock()

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def post(self, url: str, json: dict[str, Any]) -> None:
            posted(url, json)

    with patch.object(httpx, "AsyncClient", lambda **kw: _Client()):
        await runner.send_callback(definition, state)

    posted.assert_called_once()
    url, payload = posted.call_args.args
    assert url == "https://cb.example.com/hook"
    assert payload["outputs"] == {"answer": 42}
    assert payload["cost_usd"] == 0.05


async def test_send_callback_swallows_post_error() -> None:
    runner = WorkflowRunner(compiler=MagicMock())
    definition = _wf(callback=CallbackConfig(url="https://cb.example.com/hook"))
    state = {"run_id": "r1", "status": WorkflowRunStatus.COMPLETE, "outputs": {}}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def post(self, *args: Any, **kwargs: Any) -> None:
            raise httpx.ConnectError("refused")

    with patch.object(httpx, "AsyncClient", lambda **kw: _Client()):
        # Must not raise — callback failures are logged and swallowed.
        await runner.send_callback(definition, state)
