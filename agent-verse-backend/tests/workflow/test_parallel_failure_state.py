"""A step failure must not crash the graph when parallel steps settle together.

Reproduces the user-reported "workflow hangs / ends in a LangGraph error" case: a
failing step (default ``on_failure: pause``) marks the run PAUSED, then every
step in the next superstep sees ``paused_by`` and also returns
``status: PAUSED``. ``status`` had no reducer, so two parallel writes in one
superstep raised ``InvalidUpdateError: At key 'status': Can receive only one
value per step`` and the run died with that message instead of pausing on the
step that actually failed.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.registry import StepTypeRegistry
from app.workflow.state import WorkflowRunStatus

pytestmark = pytest.mark.asyncio


class _OkNode:
    def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
        self.step = step

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        return {"step_outputs": {self.step.id: {"ok": True}}}


class _BoomNode:
    def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
        self.step = step

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"{self.step.id} exploded")


@pytest.fixture(autouse=True)
def _fake_step_types() -> Any:
    saved = dict(StepTypeRegistry._registry)  # type: ignore[attr-defined]
    StepTypeRegistry._registry["ok_step"] = _OkNode  # type: ignore[attr-defined,assignment]
    StepTypeRegistry._registry["boom_step"] = _BoomNode  # type: ignore[attr-defined,assignment]
    try:
        yield
    finally:
        StepTypeRegistry._registry = saved  # type: ignore[attr-defined]


def _state(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "workflow_id": "wf",
        "tenant_id": "t1",
        "status": WorkflowRunStatus.RUNNING,
        "inputs": {},
        "step_outputs": {},
        "vars": {},
        "is_test_run": True,
    }


async def _run(steps: list[StepDefinition]) -> dict[str, Any]:
    wf = WorkflowDefinition(id="wf", name="parallel-failure", steps=steps)
    compiled = WorkflowCompiler(context_resolver=ContextResolver()).compile(wf)
    return dict(await compiled.ainvoke(_state("r1"), {"configurable": {"thread_id": "r1"}}))


async def test_failure_before_parallel_children_pauses_instead_of_crashing() -> None:
    final = await _run(
        [
            StepDefinition(id="root", type="boom_step"),
            StepDefinition(id="left", type="ok_step", depends_on=["root"]),
            StepDefinition(id="right", type="ok_step", depends_on=["root"]),
        ]
    )
    assert final["status"] == WorkflowRunStatus.PAUSED
    assert final["error_step_id"] == "root"
    assert "root exploded" in final["error"]
    assert "left" not in final["step_outputs"]
    assert "right" not in final["step_outputs"]


async def test_user_digest_shape_parallel_failure_pauses_on_failing_step() -> None:
    """The user's workflow shape: set -> (fetch ok || search fails) -> 2 children."""
    final = await _run(
        [
            StepDefinition(id="set_company", type="ok_step"),
            StepDefinition(id="fetch_repos", type="ok_step", depends_on=["set_company"]),
            StepDefinition(id="search_news", type="boom_step", depends_on=["set_company"]),
            StepDefinition(
                id="synthesize", type="ok_step", depends_on=["fetch_repos", "search_news"]
            ),
            StepDefinition(id="momentum_score", type="ok_step", depends_on=["fetch_repos"]),
        ]
    )
    assert final["status"] == WorkflowRunStatus.PAUSED
    assert final["error_step_id"] == "search_news"
    assert "synthesize" not in final["step_outputs"]


async def test_two_parallel_failures_in_one_superstep_do_not_crash() -> None:
    final = await _run(
        [
            StepDefinition(id="root", type="ok_step"),
            StepDefinition(id="a", type="boom_step", depends_on=["root"]),
            StepDefinition(id="b", type="boom_step", depends_on=["root"]),
        ]
    )
    assert final["status"] == WorkflowRunStatus.PAUSED
    assert final["error_step_id"] in {"a", "b"}
