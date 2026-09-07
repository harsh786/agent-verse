"""P0-4: grounding must gate, not merely annotate."""

from __future__ import annotations

from app.agent.grounding import check_grounding


def test_zero_tolerance_flags_one_ungrounded_claim():
    """FAILS TODAY: non-strict tolerates up to 25% ungrounded."""
    output = "The total is 41200 and the count is 999."
    tool_outputs = ["total is 41200"]  # 999 absent
    result = check_grounding(output, tool_outputs, strict=False, max_ungrounded_ratio=0.0)
    assert result.grounded is False, "one fabricated number must fail at zero tolerance"
    assert "999" in " ".join(result.ungrounded_claims)


def test_no_tool_outputs_is_not_grounded_when_claims_exist():
    """FAILS TODAY: early return treats absent evidence as grounded (fail-open)."""
    result = check_grounding("The total is 41200.", [], strict=False)
    assert result.grounded is False, "claims with no evidence must not pass"
    assert "41200" in " ".join(result.ungrounded_claims)


def test_no_claims_is_grounded():
    """Regression guard: output with no extractable claims is trivially grounded."""
    result = check_grounding("The deployment finished.", ["irrelevant evidence"])
    assert result.grounded is True


def test_state_has_consecutive_ungrounded_counter():
    """FAILS TODAY: consecutive_ungrounded does not exist."""
    from app.agent.state import AgentState
    from app.tenancy.context import PlanTier, TenantContext

    state = AgentState(
        goal="g", tenant_ctx=TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")
    )
    assert hasattr(state, "consecutive_ungrounded")
    assert state.consecutive_ungrounded == 0
