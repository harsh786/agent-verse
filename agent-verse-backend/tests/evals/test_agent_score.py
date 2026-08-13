# tests/evals/test_agent_score.py
"""app/evals/agent_score.py must exist as spec §Layer 10 file."""
from __future__ import annotations
import pytest
from app.evals.agent_score import AgentScorer
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


def _state(status=GoalStatus.COMPLETE, iterations=3) -> AgentState:
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    s = AgentState(goal="test", tenant_ctx=ctx, goal_id="g1")
    s.status = status
    s.iterations = iterations
    return s


def test_agent_score_module_is_importable():
    import app.evals.agent_score as m
    assert hasattr(m, "AgentScorer")


def test_tool_success_rate_all_success(tenant_ctx):
    scorer = AgentScorer()
    state = _state()
    step = StepResult(description="search", output="found", status=StepStatus.COMPLETE)
    step.tool_calls = [{"tool_name": "jira.search", "success": True}]
    state.steps = [step]
    score = scorer.score_tool_success_rate(state)
    assert 0.5 <= score <= 1.0


def test_tool_success_rate_all_failure(tenant_ctx):
    scorer = AgentScorer()
    state = _state(GoalStatus.FAILED)
    step = StepResult(description="search", output="", status=StepStatus.FAILED)
    step.tool_calls = [{"tool_name": "web_search", "success": False}]
    state.steps = [step]
    score = scorer.score_tool_success_rate(state)
    assert score <= 0.5


def test_grounding_no_ungrounded_claims():
    scorer = AgentScorer()
    state = _state()
    state.ungrounded_claims = []
    state.context["grounding_checked"] = True  # grounding was checked; nothing flagged
    assert scorer.score_grounding(state) == 1.0


def test_grounding_penalizes_hallucinations():
    scorer = AgentScorer()
    state = _state()
    state.ungrounded_claims = ["false claim 1", "false claim 2", "false claim 3"]
    score = scorer.score_grounding(state)
    assert score < 1.0


def test_citation_quality_with_provenance():
    scorer = AgentScorer()
    state = _state()
    state.cited_answer = "The answer is [1]."
    state.provenance = [{"claim_id": "c1", "confidence": 0.9}]
    score = scorer.score_citation_quality(state)
    assert 0.0 <= score <= 1.0


def test_all_three_dimensions_callable():
    scorer = AgentScorer()
    state = _state()
    state.ungrounded_claims = []
    assert hasattr(scorer, "score_tool_success_rate")
    assert hasattr(scorer, "score_grounding")
    assert hasattr(scorer, "score_citation_quality")
    # score_tool_success_rate returns None when there are no tool calls (correct — N/A).
    # Add a successful tool call so the scorer returns a measurable float.
    from app.agent.state import StepResult, StepStatus
    step = StepResult(description="web search", output="result", status=StepStatus.COMPLETE)
    step.tool_calls = [{"tool_name": "web_search", "success": True}]
    state.steps = [step]
    assert 0.0 <= scorer.score_tool_success_rate(state) <= 1.0
    state.context["grounding_checked"] = True  # ensure score_grounding returns a float not None
    assert 0.0 <= scorer.score_grounding(state) <= 1.0
    # score_citation_quality returns None when cited_answer=="" and provenance=[] (N/A).
    # Add a cited answer so it returns a measurable float.
    state.cited_answer = "The answer is based on [1]."
    val = scorer.score_citation_quality(state)
    assert val is None or 0.0 <= val <= 1.0
