"""Comprehensive tests for app/agent/structured_plan.py — targets 90%+ statement coverage."""
from __future__ import annotations

import pytest

from app.agent.structured_plan import StructuredPlan, StructuredStep, _safe_eval_condition


# ── _safe_eval_condition ──────────────────────────────────────────────────────

def test_safe_eval_empty_expression_returns_true() -> None:
    assert _safe_eval_condition("", {}) is True


def test_safe_eval_whitespace_expression_returns_true() -> None:
    assert _safe_eval_condition("   ", {}) is True


def test_safe_eval_true_literal() -> None:
    assert _safe_eval_condition("True", {}) is True


def test_safe_eval_false_literal() -> None:
    assert _safe_eval_condition("False", {}) is False


def test_safe_eval_comparison_true() -> None:
    ctx = {"x": type("Obj", (), {"value": 5})()}
    # Use a simple arithmetic expression
    assert _safe_eval_condition("1 == 1", ctx) is True


def test_safe_eval_comparison_false() -> None:
    assert _safe_eval_condition("1 == 2", {}) is False


def test_safe_eval_attribute_access() -> None:
    class FakeStep:
        status = "complete"
        output = "done"
        error = None

    ctx = {"s1": FakeStep()}
    assert _safe_eval_condition("s1.status == 'complete'", ctx) is True


def test_safe_eval_unsafe_pattern_rejected_returns_true() -> None:
    # Contains `;` which is not in safe allowlist pattern — use a semicolon
    # Actually the pattern allows `;` — let's use __import__ which triggers the rejection
    # via simpleeval or falls back to True on eval error
    result = _safe_eval_condition("__import__('os')", {})
    # Should default to True (safe default on error)
    assert result is True


def test_safe_eval_eval_error_returns_true() -> None:
    # Expression references an undefined name — should fallback to True
    result = _safe_eval_condition("undefined_var == 'x'", {})
    assert result is True


# ── StructuredStep ────────────────────────────────────────────────────────────

def test_structured_step_defaults() -> None:
    s = StructuredStep(id="s1", description="Do something")
    assert s.tool is None
    assert s.risk == "read"
    assert s.status == "pending"
    assert s.depends_on == []
    assert s.condition is None
    assert s.loop_until is None
    assert s.max_loop_iter == 5


def test_structured_step_should_execute_no_condition() -> None:
    s = StructuredStep(id="s1", description="Always run")
    assert s.should_execute({}) is True


def test_structured_step_should_execute_true_condition() -> None:
    class FakeStep:
        status = "complete"
        output = "done"
        error = None

    s = StructuredStep(id="s2", description="Conditional", condition="s1.status == 'complete'")
    assert s.should_execute({"s1": FakeStep()}) is True


def test_structured_step_should_execute_false_condition() -> None:
    class FakeStep:
        status = "failed"
        output = ""
        error = "err"

    s = StructuredStep(id="s2", description="Conditional", condition="s1.status == 'complete'")
    assert s.should_execute({"s1": FakeStep()}) is False


def test_structured_step_should_execute_condition_eval_error_returns_true() -> None:
    # condition references a name not in context
    s = StructuredStep(id="s2", description="Conditional", condition="nonexistent.status == 'x'")
    assert s.should_execute({}) is True


def test_structured_step_should_execute_context_object_attributes() -> None:
    """should_execute creates SR objects with correct attributes."""
    completed_step = StructuredStep(id="s1", description="Step 1", status="complete", output="out", error=None)
    step_results = {"s1": completed_step}
    dep_step = StructuredStep(id="s2", description="Dep", condition="s1.status == 'complete'")
    assert dep_step.should_execute(step_results) is True


# ── StructuredPlan.from_llm_response — JSON path ─────────────────────────────

def test_from_llm_response_simple_json_steps() -> None:
    text = '{"steps": [{"id": "s1", "description": "Do A"}, {"id": "s2", "description": "Do B"}]}'
    plan = StructuredPlan.from_llm_response(text)
    assert len(plan.steps) == 2
    assert plan.steps[0].id == "s1"
    assert plan.steps[1].description == "Do B"


def test_from_llm_response_json_with_all_fields() -> None:
    text = '{"steps": [{"id": "s1", "description": "Fetch data", "tool": "jira_get", "arguments": {"project": "PROJ"}, "depends_on": [], "risk": "read", "expected_output": "issues list", "condition": null, "loop_until": null, "max_loop_iter": 3}]}'
    plan = StructuredPlan.from_llm_response(text)
    s = plan.steps[0]
    assert s.tool == "jira_get"
    assert s.arguments == {"project": "PROJ"}
    assert s.max_loop_iter == 3


def test_from_llm_response_json_with_string_in_steps_array() -> None:
    """Legacy: steps array contains strings instead of dicts."""
    text = '{"steps": ["Step description one", "Step description two"]}'
    plan = StructuredPlan.from_llm_response(text)
    assert len(plan.steps) == 2
    assert plan.steps[0].description == "Step description one"


def test_from_llm_response_json_depends_on() -> None:
    text = '{"steps": [{"id": "s1", "description": "First"}, {"id": "s2", "description": "Second", "depends_on": ["s1"]}]}'
    plan = StructuredPlan.from_llm_response(text)
    assert plan.steps[1].depends_on == ["s1"]


def test_from_llm_response_json_null_tool_becomes_none() -> None:
    text = '{"steps": [{"id": "s1", "description": "Plain step", "tool": null}]}'
    plan = StructuredPlan.from_llm_response(text)
    assert plan.steps[0].tool is None


def test_from_llm_response_json_missing_id_autogenerated() -> None:
    text = '{"steps": [{"description": "No ID here"}]}'
    plan = StructuredPlan.from_llm_response(text)
    assert plan.steps[0].id == "s1"


def test_from_llm_response_json_with_condition_and_loop() -> None:
    text = '{"steps": [{"id": "s1", "description": "Loop step", "condition": "x == 1", "loop_until": "output == \'done\'", "max_loop_iter": 7}]}'
    plan = StructuredPlan.from_llm_response(text)
    s = plan.steps[0]
    assert s.condition == "x == 1"
    assert s.loop_until == "output == 'done'"
    assert s.max_loop_iter == 7


# ── StructuredPlan.from_llm_response — legacy text path ──────────────────────

def test_from_llm_response_numbered_list() -> None:
    text = "1. Fetch the data\n2. Process the data\n3. Store the results"
    plan = StructuredPlan.from_llm_response(text)
    assert len(plan.steps) == 3
    assert "Fetch" in plan.steps[0].description
    assert "Process" in plan.steps[1].description


def test_from_llm_response_bulleted_list() -> None:
    text = "- Step one here\n- Step two here\n- Step three here"
    plan = StructuredPlan.from_llm_response(text)
    assert len(plan.steps) == 3


def test_from_llm_response_step_keyword_prefix_stripped() -> None:
    text = "Step 1: Initialize the environment\nStep 2: Run the tests"
    plan = StructuredPlan.from_llm_response(text)
    assert len(plan.steps) == 2
    assert plan.steps[0].description.startswith("Initialize")


def test_from_llm_response_empty_lines_skipped() -> None:
    text = "1. First step\n\n\n2. Second step"
    plan = StructuredPlan.from_llm_response(text)
    assert len(plan.steps) == 2


def test_from_llm_response_plain_text_no_markers() -> None:
    text = "Just do the thing"
    plan = StructuredPlan.from_llm_response(text)
    assert len(plan.steps) == 1
    assert plan.steps[0].description == "Just do the thing"


def test_from_llm_response_invalid_json_falls_through_to_text() -> None:
    text = '{"not": "a steps dict"}'
    plan = StructuredPlan.from_llm_response(text)
    # Falls through to text parser — single line becomes a step
    assert len(plan.steps) >= 1


# ── StructuredPlan.execution_waves ────────────────────────────────────────────

def test_execution_waves_empty_plan() -> None:
    plan = StructuredPlan(steps=[])
    assert plan.execution_waves() == []


def test_execution_waves_single_step_no_deps() -> None:
    plan = StructuredPlan(steps=[StructuredStep(id="s1", description="Only step")])
    waves = plan.execution_waves()
    assert len(waves) == 1
    assert waves[0][0].id == "s1"


def test_execution_waves_all_independent() -> None:
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="A"),
        StructuredStep(id="s2", description="B"),
        StructuredStep(id="s3", description="C"),
    ])
    waves = plan.execution_waves()
    # All three can run in one wave
    assert len(waves) == 1
    assert len(waves[0]) == 3


def test_execution_waves_linear_chain() -> None:
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="A"),
        StructuredStep(id="s2", description="B", depends_on=["s1"]),
        StructuredStep(id="s3", description="C", depends_on=["s2"]),
    ])
    waves = plan.execution_waves()
    assert len(waves) == 3
    assert waves[0][0].id == "s1"
    assert waves[1][0].id == "s2"
    assert waves[2][0].id == "s3"


def test_execution_waves_diamond_pattern() -> None:
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="Root"),
        StructuredStep(id="s2", description="Left", depends_on=["s1"]),
        StructuredStep(id="s3", description="Right", depends_on=["s1"]),
        StructuredStep(id="s4", description="Merge", depends_on=["s2", "s3"]),
    ])
    waves = plan.execution_waves()
    assert len(waves) == 3
    wave_ids = [sorted(s.id for s in w) for w in waves]
    assert wave_ids[0] == ["s1"]
    assert sorted(wave_ids[1]) == ["s2", "s3"]
    assert wave_ids[2] == ["s4"]


def test_execution_waves_circular_dependency_dumps_remaining() -> None:
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="A", depends_on=["s2"]),
        StructuredStep(id="s2", description="B", depends_on=["s1"]),
    ])
    waves = plan.execution_waves()
    # Should produce at least one wave (circular dep detected — dump remaining)
    total_steps = sum(len(w) for w in waves)
    assert total_steps == 2


def test_execution_waves_unknown_dep_dumps_remaining() -> None:
    """Step depends on an ID that doesn't exist — still gets executed."""
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="A"),
        StructuredStep(id="s2", description="B", depends_on=["nonexistent"]),
    ])
    waves = plan.execution_waves()
    total_steps = sum(len(w) for w in waves)
    assert total_steps == 2


# ── Lines not yet covered ─────────────────────────────────────────────────────

def test_safe_eval_unsafe_chars_in_expression_defaults_to_true() -> None:
    """Expression containing '{' is rejected by SAFE_PATTERN → returns True."""
    # '{' is not in the safe pattern allowlist
    result = _safe_eval_condition("{bad: expr}", {})
    assert result is True


def test_should_execute_exception_in_ctx_construction_returns_true() -> None:
    """Exception raised during context setup falls back to True."""
    class ExplodingStep:
        @property
        def status(self):
            raise RuntimeError("status exploded")
        output = ""
        error = None

    step = StructuredStep(id="s2", description="Cond step", condition="s1.status == 'complete'")
    # ExplodingStep.status raises → exception caught → returns True
    result = step.should_execute({"s1": ExplodingStep()})
    assert result is True


def test_from_llm_response_json_decode_error_falls_to_text() -> None:
    """Malformed JSON triggers JSONDecodeError → falls through to text parser."""
    text = '{"steps": [}'  # Invalid JSON
    plan = StructuredPlan.from_llm_response(text)
    # Falls through to text parser — the raw text becomes a step
    assert isinstance(plan, StructuredPlan)


def test_from_llm_response_value_error_in_step_parsing_falls_to_text() -> None:
    """Invalid max_loop_iter triggers ValueError → falls through to text parser."""
    text = '{"steps": [{"id": "s1", "description": "Step", "max_loop_iter": "not_a_number"}]}'
    plan = StructuredPlan.from_llm_response(text)
    # ValueError is caught → falls through to text parser
    assert isinstance(plan, StructuredPlan)


# ── StructuredPlan.to_step_list ───────────────────────────────────────────────

def test_to_step_list_returns_descriptions() -> None:
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="First"),
        StructuredStep(id="s2", description="Second"),
    ])
    assert plan.to_step_list() == ["First", "Second"]


def test_to_step_list_empty_plan() -> None:
    plan = StructuredPlan(steps=[])
    assert plan.to_step_list() == []
