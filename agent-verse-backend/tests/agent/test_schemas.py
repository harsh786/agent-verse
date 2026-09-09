"""Tests for Phase 3 Track A — PlannerPlan and VerifierVerdict schemas."""

import json

from app.agent.schemas import (
    PlannerPlan,
    VerifierVerdict,
    parse_verifier_verdict,
    planner_schema,
    verifier_schema,
)


class TestPlannerSchema:
    def test_schema_has_steps_array(self) -> None:
        schema = planner_schema()
        assert schema["properties"]["steps"]["type"] == "array"
        assert "steps" in schema["required"]

    def test_planner_plan_model(self) -> None:
        plan = PlannerPlan(steps=["step 1", "step 2"])
        assert len(plan.steps) == 2

    def test_planner_plan_from_json(self) -> None:
        raw = '{"steps": ["search jira", "create report"], "reasoning": "direct approach"}'
        data = json.loads(raw)
        plan = PlannerPlan(**data)
        assert plan.steps[0] == "search jira"


class TestVerifierSchema:
    def test_schema_has_success_and_reason(self) -> None:
        schema = verifier_schema()
        assert "success" in schema["required"]
        assert "reason" in schema["required"]

    def test_verifier_verdict_model(self) -> None:
        v = VerifierVerdict(success=True, reason="Goal achieved")
        assert v.success is True
        assert v.retry is True  # default

    def test_confidence_range(self) -> None:
        v = VerifierVerdict(success=True, reason="ok", confidence=0.9)
        assert 0.0 <= v.confidence <= 1.0


class TestParseVerifierVerdict:
    def test_parses_json(self) -> None:
        raw = '{"success": true, "reason": "all steps completed"}'
        result = parse_verifier_verdict(raw)
        assert result["success"] is True
        assert "completed" in result["reason"]

    def test_parses_json_false(self) -> None:
        raw = '{"success": false, "reason": "step 2 failed"}'
        result = parse_verifier_verdict(raw)
        assert result["success"] is False

    def test_parses_markdown_json(self) -> None:
        raw = '```json\n{"success": true, "reason": "ok"}\n```'
        result = parse_verifier_verdict(raw)
        assert result["success"] is True

    def test_text_fallback_success_keywords(self) -> None:
        raw = "Goal achieved successfully. All steps completed."
        result = parse_verifier_verdict(raw)
        assert result["success"] is True

    def test_text_fallback_failure_keywords(self) -> None:
        raw = "The goal was not achieved. Step 3 failed."
        result = parse_verifier_verdict(raw)
        assert result["success"] is False

    def test_defaults_to_failed_on_unclear(self) -> None:
        result = parse_verifier_verdict("something happened")
        assert result["success"] is False  # fail-safe

    def test_ungrounded_claims_field(self) -> None:
        raw = '{"success": false, "reason": "ungrounded", "ungrounded_claims": ["JIRA-999"]}'
        result = parse_verifier_verdict(raw)
        assert "JIRA-999" in result.get("ungrounded_claims", [])
