"""Tests for Phase 10 — Agent Collaboration and Phase 11 — Intelligence."""

from __future__ import annotations

import pytest

from app.collab.agent_collab import AgentCollabSession, CollabRound, ConsensusResult
from app.intelligence.explainability import DecisionTrace
from app.intelligence.guardrails import GuardrailChecker
from app.intelligence.eval import EvalResult, EvalScorecard
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-a", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")


# ── AgentCollabSession ────────────────────────────────────────────────────────

def test_collab_session_starts_with_proposal() -> None:
    session = AgentCollabSession(goal="Design the auth system")
    session.add_round(CollabRound(
        agent_id="agent-a",
        round_type="propose",
        content="Use JWT with 15-min expiry",
    ))
    assert len(session.rounds) == 1
    assert session.rounds[0].round_type == "propose"


def test_collab_session_reaches_consensus() -> None:
    session = AgentCollabSession(goal="Design the auth system")
    session.add_round(CollabRound(agent_id="a", round_type="propose", content="JWT approach"))
    session.add_round(CollabRound(agent_id="b", round_type="critique", content="Add refresh tokens"))
    session.add_round(CollabRound(agent_id="a", round_type="counter", content="OK, JWT + refresh"))
    session.add_round(CollabRound(agent_id="b", round_type="agree", content="Agreed"))
    consensus = session.synthesize_consensus()
    assert isinstance(consensus, ConsensusResult)
    assert consensus.agreed is True


def test_collab_session_no_consensus_without_agreement() -> None:
    session = AgentCollabSession(goal="Design the auth system")
    session.add_round(CollabRound(agent_id="a", round_type="propose", content="JWT"))
    consensus = session.synthesize_consensus()
    assert consensus.agreed is False


# ── DecisionTrace ─────────────────────────────────────────────────────────────

def test_decision_trace_records_reasoning() -> None:
    trace = DecisionTrace(
        action="Call github.create_pr",
        reasoning="User asked to create a PR for the fix",
        evidence=["Step 3 output: diff created", "Step 4 output: tests pass"],
        alternatives=["create_issue instead", "skip for now"],
        confidence=0.92,
    )
    assert trace.action == "Call github.create_pr"
    assert 0.0 <= trace.confidence <= 1.0
    assert len(trace.evidence) == 2
    assert len(trace.alternatives) == 2


def test_decision_trace_serializes() -> None:
    trace = DecisionTrace(
        action="call_tool",
        reasoning="needed",
        evidence=[],
        alternatives=[],
        confidence=0.8,
    )
    d = trace.to_dict()
    assert d["action"] == "call_tool"
    assert "confidence" in d


# ── GuardrailChecker ──────────────────────────────────────────────────────────

def test_guardrail_blocks_hallucinated_tool() -> None:
    checker = GuardrailChecker(known_tools={"github.create_pr", "slack.send_message"})
    result = checker.check_tool_call(tool_name="github.delete_everything")
    assert result.blocked is True
    assert "unknown" in result.reason.lower()


def test_guardrail_allows_known_tool() -> None:
    checker = GuardrailChecker(known_tools={"github.create_pr", "slack.send_message"})
    result = checker.check_tool_call(tool_name="github.create_pr")
    assert result.blocked is False


def test_guardrail_empty_known_tools_allows_all() -> None:
    checker = GuardrailChecker(known_tools=set())
    result = checker.check_tool_call(tool_name="anything")
    assert result.blocked is False


# ── EvalScorecard ─────────────────────────────────────────────────────────────

def test_eval_scorecard_computes_pass() -> None:
    scorecard = EvalScorecard(
        goal_id="gid-1",
        scores={
            "task_completion": 0.90,
            "accuracy": 0.85,
            "efficiency": 0.80,
            "safety": 0.95,
            "coherence": 0.88,
        },
    )
    assert scorecard.average_score() >= 0.70  # 70% pass threshold
    assert scorecard.passed() is True


def test_eval_scorecard_computes_fail() -> None:
    scorecard = EvalScorecard(
        goal_id="gid-1",
        scores={
            "task_completion": 0.50,
            "accuracy": 0.45,
            "efficiency": 0.40,
            "safety": 0.60,
            "coherence": 0.55,
        },
    )
    assert scorecard.passed() is False
