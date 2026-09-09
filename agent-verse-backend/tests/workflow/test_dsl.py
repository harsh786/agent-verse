"""Tests for WorkflowDefinition DSL parser and validator."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.workflow.dsl import StepDefinition, WorkflowDefinition

MINIMAL_YAML = """
name: Test Workflow
steps:
  - id: step1
    type: tool
    tool: ocr.extract_document
    input:
      url: "{{inputs.doc_url}}"
"""

LINEAR_YAML = """
name: Linear
steps:
  - id: a
    type: tool
    tool: some.tool
  - id: b
    type: tool
    tool: other.tool
    depends_on: [a]
"""


def test_parse_minimal_yaml():
    wf = WorkflowDefinition.from_yaml(MINIMAL_YAML)
    assert wf.name == "Test Workflow"
    assert len(wf.steps) == 1
    assert wf.steps[0].id == "step1"
    assert wf.steps[0].type == "tool"


def test_parse_linear_dependencies():
    wf = WorkflowDefinition.from_yaml(LINEAR_YAML)
    assert wf.steps[1].depends_on == ["a"]


def test_unique_step_ids():
    with pytest.raises(ValidationError):
        WorkflowDefinition.from_json({
            "name": "bad",
            "steps": [
                {"id": "dup", "type": "tool"},
                {"id": "dup", "type": "tool"},
            ]
        })


def test_unknown_depends_on():
    with pytest.raises(ValidationError):
        WorkflowDefinition.from_json({
            "name": "bad",
            "steps": [
                {"id": "a", "type": "tool", "depends_on": ["nonexistent"]},
            ]
        })


def test_circular_dependency_detected():
    with pytest.raises(ValidationError):
        WorkflowDefinition.from_json({
            "name": "bad",
            "steps": [
                {"id": "a", "type": "tool", "depends_on": ["b"]},
                {"id": "b", "type": "tool", "depends_on": ["a"]},
            ]
        })


def test_enum_input_validation():
    wf = WorkflowDefinition.from_json({
        "name": "enum_test",
        "inputs": {
            "doc_type": {
                "type": "string",
                "enum": ["pan", "aadhaar"],
            }
        },
        "steps": []
    })
    assert wf.inputs["doc_type"].enum == ["pan", "aadhaar"]


def test_from_json_round_trip():
    wf = WorkflowDefinition.from_yaml(LINEAR_YAML)
    json_data = wf.to_json()
    wf2 = WorkflowDefinition.from_json(json_data)
    assert wf2.name == wf.name
    assert len(wf2.steps) == len(wf.steps)


def test_to_yaml():
    wf = WorkflowDefinition.from_yaml(MINIMAL_YAML)
    yaml_out = wf.to_yaml()
    assert "Test Workflow" in yaml_out
    assert "step1" in yaml_out


def test_trigger_config_defaults():
    wf = WorkflowDefinition.from_json({"name": "t", "steps": []})
    assert wf.trigger.type == "api"


def test_error_handling_defaults():
    wf = WorkflowDefinition.from_json({"name": "t", "steps": []})
    assert wf.error_handling.on_step_failure == "pause"


def test_vars_section():
    wf = WorkflowDefinition.from_json({
        "name": "t",
        "vars": {"counter": 0, "flag": True},
        "steps": []
    })
    assert wf.vars["counter"] == 0
    assert wf.vars["flag"] is True


def test_callback_url_config():
    wf = WorkflowDefinition.from_json({
        "name": "t",
        "callback": {"url": "https://example.com/cb", "on_failure": True},
        "steps": []
    })
    assert wf.callback is not None
    assert wf.callback.url == "https://example.com/cb"


def test_concurrency_config():
    wf = WorkflowDefinition.from_json({
        "name": "t",
        "concurrency": {"max_concurrent_runs": 5},
        "steps": []
    })
    assert wf.concurrency.max_concurrent_runs == 5
