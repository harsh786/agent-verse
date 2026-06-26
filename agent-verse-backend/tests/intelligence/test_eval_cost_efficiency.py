"""Tests for cost-aware efficiency scoring."""
import pytest
from app.intelligence.eval_runner import EvalRunner
from app.agent.state import AgentState, GoalStatus
from app.tenancy.context import TenantContext, PlanTier


CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


def _state(cost: float = 0.0, iterations: int = 3) -> AgentState:
    s = AgentState(goal="test", tenant_ctx=CTX, goal_id="g1")
    s.status = GoalStatus.COMPLETE
    s.iterations = iterations
    s.verification_success = True
    s.steps = ["step1"]
    s.context = {"total_cost_usd": cost}
    return s


def test_zero_cost_full_efficiency_score():
    """Zero LLM cost → cost component is 1.0."""
    runner = EvalRunner()
    sc = runner.score(state=_state(cost=0.0, iterations=1), tenant_ctx=CTX)
    assert sc.scores["efficiency"] > 0.9


def test_high_cost_lower_efficiency():
    """High LLM cost reduces efficiency score."""
    runner = EvalRunner()
    sc_cheap = runner.score(state=_state(cost=0.01), tenant_ctx=CTX)
    sc_expensive = runner.score(state=_state(cost=1.50), tenant_ctx=CTX)
    assert sc_cheap.scores["efficiency"] >= sc_expensive.scores["efficiency"]


def test_many_iterations_lower_efficiency():
    """More iterations → lower efficiency."""
    runner = EvalRunner()
    sc_few = runner.score(state=_state(iterations=2), tenant_ctx=CTX)
    sc_many = runner.score(state=_state(iterations=12), tenant_ctx=CTX)
    assert sc_few.scores["efficiency"] > sc_many.scores["efficiency"]
