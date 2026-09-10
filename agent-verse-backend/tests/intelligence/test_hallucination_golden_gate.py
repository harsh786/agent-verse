"""
Golden-set regression gate for hallucination reduction (Phase 3 Track F).

These tests run against the fixed golden fixtures and FAIL if the grounding
checker produces wrong verdicts. No LLM calls — purely deterministic.
"""
import json
from pathlib import Path

import pytest

GOLDEN_FIXTURES_PATH = Path(__file__).parent.parent / "fixtures" / "hallucination_golden.json"


def load_golden_cases():
    with open(GOLDEN_FIXTURES_PATH) as f:
        data = json.load(f)
    return data["cases"]


class TestGroundingGoldenGate:
    """C1 regression gate: grounding must produce correct verdicts on golden set."""

    @pytest.mark.parametrize("case", load_golden_cases())
    def test_grounding_on_golden_case(self, case):
        from app.agent.grounding import check_grounding
        result = check_grounding(
            case["step_output"],
            case["tool_outputs"],
        )
        if case["expected_grounded"]:
            assert result.grounded is True, (
                f"GROUNDING REGRESSION [{case['id']}]: {case['name']}\n"
                f"Expected GROUNDED but got UNGROUNDED\n"
                f"Ungrounded claims: {result.ungrounded_claims}"
            )
        else:
            assert result.grounded is False, (
                f"GROUNDING REGRESSION [{case['id']}]: {case['name']}\n"
                f"Expected UNGROUNDED but got GROUNDED\n"
                f"Step output: {case['step_output']}\n"
                f"Tool outputs: {case['tool_outputs']}"
            )

    def test_all_golden_cases_loaded(self):
        cases = load_golden_cases()
        assert len(cases) >= 5, f"Expected ≥5 golden cases, got {len(cases)}"

    def test_grounding_false_claim_rate_on_golden_set(self):
        """Ungrounded-claim rate on golden set must be below 50% (when all expected-grounded cases pass)."""
        from app.agent.grounding import check_grounding
        cases = load_golden_cases()
        grounded_cases = [c for c in cases if c["expected_grounded"]]

        if not grounded_cases:
            return

        ungrounded_count = 0
        total_claims = 0
        for case in grounded_cases:
            result = check_grounding(case["step_output"], case["tool_outputs"])
            ungrounded_count += len(result.ungrounded_claims)
            total_claims += result.checked_claims

        if total_claims > 0:
            false_claim_rate = ungrounded_count / total_claims
            assert false_claim_rate <= 0.50, (
                f"Too many ungrounded claims in grounded golden cases: {false_claim_rate:.1%}"
            )
