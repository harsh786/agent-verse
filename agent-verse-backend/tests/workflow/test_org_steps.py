"""2.W-8: the four org-level step types must be registered and actually execute.

They existed in ``app/workflow/steps/org_steps.py`` but were (a) never registered
with ``StepTypeRegistry`` (so a workflow using them failed DSL validation opaquely)
and (b) written against a stale API — ``self._step.config`` (the DSL field is
``input``) and attribute access ``state.vars`` / ``state.step_outputs`` on what is
a ``TypedDict`` — so they raised ``AttributeError`` if ever reached.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.workflow.dsl import StepDefinition
from app.workflow.registry import StepTypeRegistry

pytestmark = pytest.mark.asyncio

_ORG_TYPES = ["department_handoff", "cross_team_review", "org_decision", "parallel_departments"]


@pytest.mark.parametrize("step_type", _ORG_TYPES)
async def test_org_step_type_registered(step_type: str) -> None:
    assert StepTypeRegistry.is_registered(step_type), f"{step_type} not registered"


def _make(step_type: str, **input_cfg: Any) -> Any:
    node_cls = StepTypeRegistry.get(step_type)
    step = StepDefinition(id="s1", type=step_type, input=input_cfg)
    return node_cls(step, MagicMock())


async def test_org_decision_executes_against_current_state_api() -> None:
    node = _make("org_decision", problem="ship or wait?", options=["ship", "wait"])
    state = {"vars": {}, "step_outputs": {}}
    result = await node.execute(state)  # must not raise AttributeError
    out = result["step_outputs"]["s1"]
    assert out["chosen_option"] == "ship"
    assert out["problem"] == "ship or wait?"


async def test_department_handoff_reads_input_and_state() -> None:
    node = _make("department_handoff", target_department="qa", artifact_key="draft")
    state = {"vars": {"draft": {"doc": "v1"}}, "step_outputs": {}}
    result = await node.execute(state)
    out = result["step_outputs"]["s1"]
    assert out["handoff_to"] == "qa"
    assert out["artifact"] == {"doc": "v1"}


async def test_parallel_departments_executes() -> None:
    node = _make("parallel_departments", departments=["eng", "design"])
    result = await node.execute({"vars": {}, "step_outputs": {}})
    out = result["step_outputs"]["s1"]
    assert out["departments"] == ["eng", "design"]
    assert set(out["dept_tasks"]) == {"eng", "design"}
