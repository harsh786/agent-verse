"""Tests for StructuredPlan — parsing and topological ordering."""
from __future__ import annotations

import pytest

from app.agent.structured_plan import StructuredPlan, StructuredStep


# ── parsing ───────────────────────────────────────────────────────────────────


def test_parse_structured_json_format() -> None:
    """Full structured JSON with id/tool/depends_on is parsed correctly."""
    text = """{
  "steps": [
    {
      "id": "s1",
      "description": "Fetch ticket details",
      "tool": "jira.get_issue",
      "arguments": {"issue_id": "PROJ-123"},
      "depends_on": [],
      "risk": "read",
      "expected_output": "issue dict"
    },
    {
      "id": "s2",
      "description": "Post comment",
      "tool": "jira.comment",
      "arguments": {"body": "done"},
      "depends_on": ["s1"],
      "risk": "write_low",
      "expected_output": "comment id"
    }
  ]
}"""
    plan = StructuredPlan.from_llm_response(text)
    assert len(plan.steps) == 2
    assert plan.steps[0].id == "s1"
    assert plan.steps[0].tool == "jira.get_issue"
    assert plan.steps[0].risk == "read"
    assert plan.steps[1].id == "s2"
    assert plan.steps[1].depends_on == ["s1"]


def test_parse_legacy_text_list_format() -> None:
    """Numbered text list (legacy planner output) is parsed into steps."""
    text = "1. Research the topic\n2. Write a summary\n3. Send the report"
    plan = StructuredPlan.from_llm_response(text)
    assert len(plan.steps) == 3
    assert "Research" in plan.steps[0].description
    assert "Write" in plan.steps[1].description
    assert "Send" in plan.steps[2].description


def test_parse_legacy_bulleted_list() -> None:
    """Bulleted list lines are parsed as separate steps."""
    text = "- Step one\n- Step two\n- Step three"
    plan = StructuredPlan.from_llm_response(text)
    assert len(plan.steps) == 3
    assert "Step one" in plan.steps[0].description


def test_parse_malformed_json_does_not_crash() -> None:
    """Malformed JSON must not raise; function returns a StructuredPlan."""
    plan = StructuredPlan.from_llm_response("{broken: not valid json}")
    assert isinstance(plan, StructuredPlan)


def test_parse_empty_string_returns_empty_plan() -> None:
    """Empty input produces a plan with zero steps."""
    plan = StructuredPlan.from_llm_response("")
    assert isinstance(plan, StructuredPlan)
    assert len(plan.steps) == 0


def test_parse_tool_null_is_none() -> None:
    """A step with ``tool: null`` must have ``step.tool is None``."""
    text = '{"steps": [{"id": "s1", "description": "do thing", "tool": null, "depends_on": [], "risk": "read"}]}'
    plan = StructuredPlan.from_llm_response(text)
    assert len(plan.steps) == 1
    assert plan.steps[0].tool is None


# ── execution_waves ───────────────────────────────────────────────────────────


def test_execution_waves_serial() -> None:
    """A strictly serial chain A→B→C produces three single-step waves."""
    plan = StructuredPlan(
        steps=[
            StructuredStep(id="a", description="step A"),
            StructuredStep(id="b", description="step B", depends_on=["a"]),
            StructuredStep(id="c", description="step C", depends_on=["b"]),
        ]
    )
    waves = plan.execution_waves()
    assert len(waves) == 3
    assert waves[0][0].id == "a"
    assert waves[1][0].id == "b"
    assert waves[2][0].id == "c"


def test_execution_waves_fully_parallel() -> None:
    """Three independent steps collapse into a single wave."""
    plan = StructuredPlan(
        steps=[
            StructuredStep(id="a", description="step A"),
            StructuredStep(id="b", description="step B"),
            StructuredStep(id="c", description="step C"),
        ]
    )
    waves = plan.execution_waves()
    assert len(waves) == 1
    assert len(waves[0]) == 3


def test_execution_waves_mixed() -> None:
    """A→(B,C) produces two waves: [A] then [B, C]."""
    plan = StructuredPlan(
        steps=[
            StructuredStep(id="a", description="step A"),
            StructuredStep(id="b", description="step B", depends_on=["a"]),
            StructuredStep(id="c", description="step C", depends_on=["a"]),
        ]
    )
    waves = plan.execution_waves()
    assert len(waves) == 2
    assert len(waves[0]) == 1
    assert waves[0][0].id == "a"
    assert {s.id for s in waves[1]} == {"b", "c"}


def test_execution_waves_empty_plan() -> None:
    """An empty plan produces no waves."""
    plan = StructuredPlan(steps=[])
    assert plan.execution_waves() == []


# ── to_step_list ──────────────────────────────────────────────────────────────


def test_to_step_list_backward_compat() -> None:
    """to_step_list() returns plain string descriptions for legacy consumers."""
    plan = StructuredPlan(
        steps=[
            StructuredStep(id="s1", description="fetch data"),
            StructuredStep(id="s2", description="process data"),
        ]
    )
    result = plan.to_step_list()
    assert result == ["fetch data", "process data"]
