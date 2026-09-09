"""Tests for StepTypeRegistry — plugin system."""
from __future__ import annotations

import pytest

from app.workflow.registry import StepTypeMeta, StepTypeRegistry, UnknownStepTypeError
from app.workflow.state import WorkflowState


class FakeCustomNode:
    def __init__(self, step, context_resolver, **services):
        self.step = step

    async def execute(self, state: WorkflowState):
        return {"step_outputs": {self.step.id: {"custom": True}}}


def test_all_14_builtin_types_registered():
    for t in ["tool","llm","rag","http","hitl","parallel","conditional",
              "foreach","transform","sub_workflow","wait","code",
              "set_variable","emit_event"]:
        assert StepTypeRegistry.is_registered(t), f"Missing: {t}"


def test_get_builtin_type():
    NodeClass = StepTypeRegistry.get("tool")
    assert NodeClass is not None


def test_get_unknown_type_raises():
    with pytest.raises(UnknownStepTypeError):
        StepTypeRegistry.get("nonexistent_type_xyz")


def test_register_custom_type():
    meta = StepTypeMeta(
        step_type="custom.test",
        display_name="Custom Test",
        category="Custom",
        icon="custom",
        input_schema={},
        output_schema={},
        description="A test custom step",
        is_built_in=False,
    )
    StepTypeRegistry.register("custom.test", FakeCustomNode, meta)
    assert StepTypeRegistry.is_registered("custom.test")
    NodeClass = StepTypeRegistry.get("custom.test")
    assert NodeClass is FakeCustomNode
    # Cleanup
    if "custom.test" in StepTypeRegistry._registry:
        del StepTypeRegistry._registry["custom.test"]
        del StepTypeRegistry._metadata["custom.test"]


def test_list_all_returns_metadata():
    all_meta = StepTypeRegistry.list_all()
    assert len(all_meta) >= 14
    types = {m.step_type for m in all_meta}
    assert "tool" in types
    assert "llm" in types


def test_get_meta():
    meta = StepTypeRegistry.get_meta("tool")
    assert meta.display_name == "MCP Tool"
    assert meta.category == "Tools"


def test_register_idempotent():
    """Registering the same type twice should not raise."""
    NodeClass = StepTypeRegistry.get("tool")
    meta = StepTypeRegistry.get_meta("tool")
    StepTypeRegistry.register("tool", NodeClass, meta)  # should not raise
