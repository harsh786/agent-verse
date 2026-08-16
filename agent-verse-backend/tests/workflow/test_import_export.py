"""Tests for workflow DSL import/export (YAML round-trip)."""
from __future__ import annotations

import pytest
import yaml

from app.workflow.dsl import StepDefinition, WorkflowDefinition


def _simple_wf() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="Export Test",
        description="A workflow to test YAML export",
        steps=[
            StepDefinition(id="s1", type="tool", tool="my.tool"),
            StepDefinition(id="s2", type="llm", prompt="do something", depends_on=["s1"]),
        ],
    )


# ── Export ────────────────────────────────────────────────────────────────────


def test_to_yaml_returns_string() -> None:
    wf = _simple_wf()
    result = wf.to_yaml()
    assert isinstance(result, str)
    assert len(result) > 0


def test_to_yaml_parseable() -> None:
    wf = _simple_wf()
    raw = wf.to_yaml()
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)


def test_to_yaml_contains_name() -> None:
    wf = _simple_wf()
    raw = wf.to_yaml()
    assert "Export Test" in raw


def test_to_yaml_contains_steps() -> None:
    wf = _simple_wf()
    raw = wf.to_yaml()
    assert "s1" in raw
    assert "s2" in raw


# ── Import (round-trip) ───────────────────────────────────────────────────────


def test_from_yaml_roundtrip() -> None:
    """Export to YAML then parse back — should produce equivalent definition."""
    wf = _simple_wf()
    raw = wf.to_yaml()
    parsed_data = yaml.safe_load(raw)
    wf2 = WorkflowDefinition(**parsed_data)
    assert wf2.name == wf.name
    assert len(wf2.steps) == len(wf.steps)
    assert wf2.steps[0].id == wf.steps[0].id


def test_from_json_string() -> None:
    import json
    wf = _simple_wf()
    data = wf.model_dump()
    json_str = json.dumps(data)
    wf2 = WorkflowDefinition.from_json(json_str)
    assert wf2.name == wf.name


def test_from_json_dict() -> None:
    wf = _simple_wf()
    data = wf.model_dump()
    wf2 = WorkflowDefinition.from_json(data)
    assert wf2.name == wf.name


def test_import_invalid_yaml_raises() -> None:
    with pytest.raises(Exception):
        WorkflowDefinition(**{"invalid_field_only": True})


def test_export_preserves_depends_on() -> None:
    wf = _simple_wf()
    raw = wf.to_yaml()
    parsed_data = yaml.safe_load(raw)
    wf2 = WorkflowDefinition(**parsed_data)
    s2 = next(s for s in wf2.steps if s.id == "s2")
    assert "s1" in s2.depends_on


def test_export_with_trigger() -> None:
    from app.workflow.dsl import TriggerDefinition
    wf = WorkflowDefinition(
        name="With Trigger",
        trigger=TriggerDefinition(type="schedule"),
        steps=[StepDefinition(id="s", type="tool", tool="t")],
    )
    raw = wf.to_yaml()
    assert "schedule" in raw
