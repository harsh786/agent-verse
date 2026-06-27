"""P1.1 advanced DAG tests: conditional branches and loop fields."""
import pytest


def test_structured_step_has_condition_field():
    from app.agent.structured_plan import StructuredStep
    s = StructuredStep(id="s1", description="test", condition="s0.status == 'complete'")
    assert s.condition == "s0.status == 'complete'"


def test_structured_step_has_loop_fields():
    from app.agent.structured_plan import StructuredStep
    s = StructuredStep(id="s1", description="poll", loop_until="output.startswith('done')", max_loop_iter=3)
    assert s.loop_until is not None
    assert s.max_loop_iter == 3


def test_should_execute_returns_true_when_no_condition():
    from app.agent.structured_plan import StructuredStep
    s = StructuredStep(id="s1", description="always run")
    assert s.should_execute({}) is True


def test_should_execute_evaluates_condition():
    from app.agent.structured_plan import StructuredStep
    s1 = StructuredStep(id="s1", description="done", status="complete")
    s2 = StructuredStep(id="s2", description="conditional", condition="s1.status == 'complete'")
    assert s2.should_execute({"s1": s1}) is True


def test_should_execute_blocks_on_failed_condition():
    from app.agent.structured_plan import StructuredStep
    s1 = StructuredStep(id="s1", description="failed", status="failed")
    s2 = StructuredStep(id="s2", description="only if s1 ok", condition="s1.status == 'complete'")
    assert s2.should_execute({"s1": s1}) is False


def test_should_execute_defaults_true_on_eval_error():
    from app.agent.structured_plan import StructuredStep
    s = StructuredStep(id="s1", description="bad condition", condition="INVALID SYNTAX !!!")
    assert s.should_execute({}) is True  # fail-open


def test_structured_step_has_runtime_fields():
    """status, output, error fields exist for tracking execution state."""
    from app.agent.structured_plan import StructuredStep
    s = StructuredStep(id="s1", description="test")
    assert s.status == "pending"
    assert s.output == ""
    assert s.error is None


def test_from_llm_response_parses_condition():
    """from_llm_response extracts condition field from JSON."""
    import json
    from app.agent.structured_plan import StructuredPlan
    data = {
        "steps": [
            {"id": "s1", "description": "Do A"},
            {"id": "s2", "description": "Do B", "condition": "s1.status == 'complete'"},
        ]
    }
    plan = StructuredPlan.from_llm_response(json.dumps(data))
    assert len(plan.steps) == 2
    assert plan.steps[1].condition == "s1.status == 'complete'"


def test_from_llm_response_parses_loop_fields():
    """from_llm_response extracts loop_until and max_loop_iter from JSON."""
    import json
    from app.agent.structured_plan import StructuredPlan
    data = {
        "steps": [
            {
                "id": "s1",
                "description": "Poll until done",
                "loop_until": "output.startswith('SUCCESS')",
                "max_loop_iter": 3,
            }
        ]
    }
    plan = StructuredPlan.from_llm_response(json.dumps(data))
    assert plan.steps[0].loop_until == "output.startswith('SUCCESS')"
    assert plan.steps[0].max_loop_iter == 3
