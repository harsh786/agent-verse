"""A run that stops on a failing step carries that step's error at the run level.

Found on a live stack: a digest workflow ended "failed" with an empty run-level
error while its step rows held the real errors — a code step that raised
("Subprocess execution is disabled…") and an llm step that hit its timeout.

Root causes reproduced here:
* ``status`` / ``error`` / ``error_step_id`` / ``paused_by`` had no reducer, so
  two steps failing in the same super-step (parallel branches), or two nodes
  downstream of one failure both short-circuiting to PAUSED, made LangGraph raise
  ``InvalidUpdateError`` — the run was marked failed with that framework message
  instead of the step's error, and no ``error_step_id``.
* ``_finalize_status`` never persisted ``error_step_id``, and the exception path
  (``on_failure: abort``) dropped which step failed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.registry import StepTypeRegistry
from app.workflow.runner import WorkflowRunner

_RUN = "11111111-1111-1111-1111-111111111111"
_SANDBOX_ERR = "Subprocess execution is disabled in this deployment"


class _Store:
    def __init__(self, definition: dict[str, Any]) -> None:
        self.definition = definition
        self.run: dict[str, Any] = {"status": "pending", "error": None, "error_step_id": None}
        self.steps: dict[str, dict[str, Any]] = {}

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any]:
        return {"inputs": {}}

    async def get_status(self, tenant_id: str, run_id: str) -> str:
        return str(self.run["status"])

    async def update_status(self, run_id: str, status: Any, *, tenant_id: str, **kw: Any) -> bool:
        self.run["status"] = str(getattr(status, "value", status))
        for key in ("error", "error_step_id"):
            if kw.get(key) is not None:
                self.run[key] = kw[key]
        return True

    async def get_workflow_id(self, run_id: str, tenant_id: str | None = None) -> str:
        return "wf"

    async def get_definition(self, workflow_id: str, tenant_id: str) -> dict[str, Any]:
        return self.definition

    async def record_step_start(self, *, step_id: str, **kw: Any) -> str:
        self.steps[step_id] = {"step_id": step_id, "status": "running", "error": None}
        return step_id

    async def record_step_finish(
        self, *, step_id: str, status: Any, error: str | None = None, **kw: Any
    ) -> bool:
        self.steps[step_id].update(status=str(getattr(status, "value", status)), error=error)
        return True

    async def get_step_result(self, tenant_id: str, run_id: str, step_id: str) -> Any:
        return None

    async def list_step_results(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        return list(self.steps.values())


class _SandboxDisabled:
    """Stands in for a code step whose sandbox refuses to run."""

    def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
        self.step = step

    async def execute(self, state: Any) -> dict[str, Any]:
        raise RuntimeError(_SANDBOX_ERR)


class _Slow:
    """Stands in for an llm step that outlives its step timeout."""

    def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
        self.step = step

    async def execute(self, state: Any) -> dict[str, Any]:
        await asyncio.sleep(5)
        return {"step_outputs": {self.step.id: {}}}


@pytest.fixture(autouse=True)
def _step_types() -> Any:
    saved = dict(StepTypeRegistry._registry)  # type: ignore[attr-defined]
    StepTypeRegistry._registry["sandbox_disabled_t"] = _SandboxDisabled  # type: ignore[attr-defined]
    StepTypeRegistry._registry["slow_llm_t"] = _Slow  # type: ignore[attr-defined]
    yield
    StepTypeRegistry._registry = saved  # type: ignore[attr-defined]


def _definition(*, policy: str, parallel: bool) -> dict[str, Any]:
    return WorkflowDefinition(
        name="digest",
        id="wf",
        steps=[
            StepDefinition(id="momentum_score", type="sandbox_disabled_t", on_failure=policy),
            StepDefinition(
                id="synthesize",
                type="slow_llm_t",
                timeout="0.2s",
                on_failure=policy,
                depends_on=[] if parallel else ["momentum_score"],
            ),
            StepDefinition(
                id="publish", type="transform", depends_on=["momentum_score", "synthesize"]
            ),
        ],
    ).to_json()


async def _run(definition: dict[str, Any]) -> _Store:
    store = _Store(definition)
    runner = WorkflowRunner(
        compiler=WorkflowCompiler(ContextResolver(), run_store=store), run_store=store
    )
    await runner.execute_fresh(_RUN, "wf", "t-1")
    return store


@pytest.mark.asyncio
async def test_two_parallel_step_failures_surface_a_step_error_not_a_framework_error() -> None:
    store = await _run(_definition(policy="pause", parallel=True))

    # Both steps really failed, each with its own error.
    assert store.steps["momentum_score"]["error"] == _SANDBOX_ERR
    assert store.steps["synthesize"]["error"] == "step 'synthesize' exceeded timeout 0.2s"
    # The run halted per the default on_failure (pause) — not a crash — and names
    # one of the failing steps together with that step's own error.
    assert store.run["status"] == "paused"
    failing = store.run["error_step_id"]
    assert failing in {"momentum_score", "synthesize"}
    assert store.run["error"] == store.steps[failing]["error"]
    assert "publish" not in store.steps


@pytest.mark.asyncio
async def test_failure_before_a_fan_out_keeps_the_step_error() -> None:
    """One failure, two downstream nodes short-circuiting in the same super-step."""
    store = await _run(_definition(policy="pause", parallel=False))

    assert store.run["status"] == "paused"
    assert store.run["error"] == _SANDBOX_ERR
    assert store.run["error_step_id"] == "momentum_score"


@pytest.mark.asyncio
async def test_aborting_step_exception_names_the_step() -> None:
    store = await _run(_definition(policy="abort", parallel=False))

    assert store.run["status"] == "failed"
    assert store.run["error"] == _SANDBOX_ERR
    assert store.run["error_step_id"] == "momentum_score"


@pytest.mark.asyncio
async def test_aborting_step_timeout_names_the_step() -> None:
    definition = WorkflowDefinition(
        name="digest",
        id="wf",
        steps=[
            StepDefinition(id="synthesize", type="slow_llm_t", timeout="0.2s", on_failure="abort")
        ],
    ).to_json()
    store = await _run(definition)

    assert store.run["status"] == "failed"
    assert store.run["error"] == "step 'synthesize' exceeded timeout 0.2s"
    assert store.run["error_step_id"] == "synthesize"


@pytest.mark.asyncio
async def test_failed_run_without_a_state_error_falls_back_to_the_failed_step_row() -> None:
    store = _Store(_definition(policy="pause", parallel=True))
    store.steps["momentum_score"] = {
        "step_id": "momentum_score",
        "status": "failed",
        "error": _SANDBOX_ERR,
    }
    runner = WorkflowRunner(
        compiler=WorkflowCompiler(ContextResolver(), run_store=store), run_store=store
    )
    await runner._finalize_status(_RUN, "t-1", {"status": "failed"})

    assert store.run["status"] == "failed"
    assert store.run["error"] == _SANDBOX_ERR
    assert store.run["error_step_id"] == "momentum_score"
