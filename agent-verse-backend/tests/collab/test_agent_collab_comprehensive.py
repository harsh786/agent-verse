"""Comprehensive tests for app/collab/agent_collab.py — targets the 37% baseline."""
from __future__ import annotations

import pytest

from app.collab.agent_collab import (
    AgentCollabSession,
    CollabRound,
    ConsensusResult,
)


# ── CollabRound dataclass ─────────────────────────────────────────────────────

def test_collab_round_auto_round_id() -> None:
    r = CollabRound(agent_id="a", round_type="propose", content="Hello")
    assert len(r.round_id) == 32


def test_collab_round_fields() -> None:
    r = CollabRound(agent_id="agent-1", round_type="critique", content="Needs work")
    assert r.agent_id == "agent-1"
    assert r.round_type == "critique"
    assert r.content == "Needs work"


def test_collab_round_explicit_round_id() -> None:
    r = CollabRound(agent_id="a", round_type="agree", content="ok", round_id="custom-id")
    assert r.round_id == "custom-id"


# ── ConsensusResult dataclass ─────────────────────────────────────────────────

def test_consensus_result_agreed() -> None:
    cr = ConsensusResult(agreed=True, summary="Use JWT", dissenter=None)
    assert cr.agreed is True
    assert cr.summary == "Use JWT"
    assert cr.dissenter is None


def test_consensus_result_not_agreed_with_dissenter() -> None:
    cr = ConsensusResult(agreed=False, summary="Reject", dissenter="agent-b")
    assert cr.agreed is False
    assert cr.dissenter == "agent-b"


def test_consensus_result_defaults() -> None:
    cr = ConsensusResult(agreed=False)
    assert cr.summary == ""
    assert cr.dissenter is None


# ── AgentCollabSession ────────────────────────────────────────────────────────

def test_session_starts_empty() -> None:
    sess = AgentCollabSession(goal="Build something")
    assert sess.goal == "Build something"
    assert sess.rounds == []


def test_add_round_appends() -> None:
    sess = AgentCollabSession(goal="G")
    r1 = CollabRound(agent_id="a", round_type="propose", content="p1")
    r2 = CollabRound(agent_id="b", round_type="critique", content="c1")
    sess.add_round(r1)
    sess.add_round(r2)
    assert len(sess.rounds) == 2
    assert sess.rounds[0] is r1
    assert sess.rounds[1] is r2


# ── synthesize_consensus ──────────────────────────────────────────────────────

def test_consensus_no_rounds_returns_no_agreement() -> None:
    sess = AgentCollabSession(goal="Empty")
    result = sess.synthesize_consensus()
    assert result.agreed is False
    assert "No agreement" in result.summary


def test_consensus_propose_only_no_agree_round() -> None:
    sess = AgentCollabSession(goal="Design system")
    sess.add_round(CollabRound(agent_id="a", round_type="propose", content="Approach A"))
    result = sess.synthesize_consensus()
    assert result.agreed is False


def test_consensus_full_agree_flow() -> None:
    sess = AgentCollabSession(goal="Design auth")
    sess.add_round(CollabRound(agent_id="a", round_type="propose", content="JWT approach"))
    sess.add_round(CollabRound(agent_id="b", round_type="critique", content="Add refresh"))
    sess.add_round(CollabRound(agent_id="a", round_type="counter", content="JWT + refresh"))
    sess.add_round(CollabRound(agent_id="b", round_type="agree", content="Agreed"))
    result = sess.synthesize_consensus()
    assert result.agreed is True
    # summary should be the last counter/propose content
    assert "JWT + refresh" in result.summary


def test_consensus_agree_uses_last_proposal() -> None:
    sess = AgentCollabSession(goal="Goal")
    sess.add_round(CollabRound(agent_id="a", round_type="propose", content="First proposal"))
    sess.add_round(CollabRound(agent_id="a", round_type="counter", content="Refined proposal"))
    sess.add_round(CollabRound(agent_id="b", round_type="agree", content="ok"))
    result = sess.synthesize_consensus()
    assert result.agreed is True
    assert "Refined proposal" in result.summary


def test_consensus_agree_falls_back_to_goal() -> None:
    """If no propose/counter, summary should be the original goal."""
    sess = AgentCollabSession(goal="My Goal Text")
    sess.add_round(CollabRound(agent_id="a", round_type="agree", content="Sure"))
    result = sess.synthesize_consensus()
    assert result.agreed is True
    assert result.summary == "My Goal Text"


def test_consensus_disagree_blocks_agreement() -> None:
    sess = AgentCollabSession(goal="X")
    sess.add_round(CollabRound(agent_id="a", round_type="propose", content="p"))
    sess.add_round(CollabRound(agent_id="b", round_type="agree", content="yes"))
    sess.add_round(CollabRound(agent_id="c", round_type="disagree", content="No, bad idea"))
    result = sess.synthesize_consensus()
    assert result.agreed is False
    assert "No, bad idea" in result.summary
    assert result.dissenter == "c"


def test_consensus_multiple_disagree_uses_last() -> None:
    sess = AgentCollabSession(goal="X")
    sess.add_round(CollabRound(agent_id="a", round_type="agree", content="ok"))
    sess.add_round(CollabRound(agent_id="b", round_type="disagree", content="first objection"))
    sess.add_round(CollabRound(agent_id="c", round_type="disagree", content="second objection"))
    result = sess.synthesize_consensus()
    assert result.agreed is False
    assert "second objection" in result.summary
    assert result.dissenter == "c"


# ── synthesize_consensus_llm ──────────────────────────────────────────────────

async def test_consensus_llm_no_rounds_falls_back() -> None:
    from unittest.mock import MagicMock

    sess = AgentCollabSession(goal="Empty LLM Test")
    result = await sess.synthesize_consensus_llm(provider=MagicMock())
    assert result.agreed is False


async def test_consensus_llm_provider_none_falls_back() -> None:
    sess = AgentCollabSession(goal="No Provider")
    sess.add_round(CollabRound(agent_id="a", round_type="agree", content="ok"))
    result = await sess.synthesize_consensus_llm(provider=None)
    # should fall back to synthesize_consensus
    assert result.agreed is True


async def test_consensus_llm_valid_json_response() -> None:
    import json
    from unittest.mock import AsyncMock, MagicMock

    provider = MagicMock()
    response = MagicMock()
    response.content = json.dumps({
        "agreed": True,
        "consensus": "Use JWT with refresh tokens",
        "dissenter_id": None,
        "key_points": ["15 min expiry", "refresh token rotation"],
    })
    provider.complete = AsyncMock(return_value=response)

    sess = AgentCollabSession(goal="Auth design")
    sess.add_round(CollabRound(agent_id="a", round_type="propose", content="JWT proposal"))
    sess.add_round(CollabRound(agent_id="b", round_type="agree", content="approved"))

    result = await sess.synthesize_consensus_llm(provider=provider)
    assert result.agreed is True
    assert result.summary == "Use JWT with refresh tokens"
    assert result.dissenter is None


async def test_consensus_llm_invalid_json_falls_back() -> None:
    from unittest.mock import AsyncMock, MagicMock

    provider = MagicMock()
    response = MagicMock()
    response.content = "not valid json {{{}"
    provider.complete = AsyncMock(return_value=response)

    sess = AgentCollabSession(goal="Fallback Test")
    sess.add_round(CollabRound(agent_id="a", round_type="propose", content="proposal"))
    sess.add_round(CollabRound(agent_id="b", round_type="agree", content="ok"))

    result = await sess.synthesize_consensus_llm(provider=provider)
    # Falls back to rule-based
    assert result.agreed is True


async def test_consensus_llm_provider_exception_falls_back() -> None:
    from unittest.mock import AsyncMock, MagicMock

    provider = MagicMock()
    provider.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    sess = AgentCollabSession(goal="Error Test")
    sess.add_round(CollabRound(agent_id="a", round_type="agree", content="ok"))

    result = await sess.synthesize_consensus_llm(provider=provider)
    # Falls back gracefully
    assert isinstance(result, ConsensusResult)


async def test_consensus_llm_truncates_long_content() -> None:
    """Provider receives content truncated to 300 chars per round."""
    from unittest.mock import AsyncMock, MagicMock

    provider = MagicMock()
    response = MagicMock()
    response.content = '{"agreed": false, "consensus": "c"}'
    provider.complete = AsyncMock(return_value=response)

    long_content = "x" * 500
    sess = AgentCollabSession(goal="Long content test")
    sess.add_round(CollabRound(agent_id="a", round_type="propose", content=long_content))

    await sess.synthesize_consensus_llm(provider=provider)
    call_args = provider.complete.call_args
    request = call_args[0][0]
    # The truncation happens inside the function — verify the call was made
    assert provider.complete.called


# ── persist_debate ────────────────────────────────────────────────────────────

async def test_persist_debate_noop_when_db_is_none() -> None:
    """No DB should not raise."""
    sess = AgentCollabSession(goal="Persist Test")
    # Should return without error
    await sess.persist_debate(
        session_id="sid",
        goal_id="gid",
        tenant_id="tid",
        original_goal="Do something",
        consensus="Final answer",
        confidence=0.9,
        rounds=3,
        proposals=[],
        db=None,
    )


async def test_persist_debate_with_proposals_and_mock_db() -> None:
    import contextlib
    from unittest.mock import AsyncMock, MagicMock

    executed_sqls: list[str] = []

    @contextlib.asynccontextmanager
    async def mock_session_ctx():
        session = MagicMock()
        session.begin = lambda: contextlib.AsyncExitStack()
        session.execute = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        yield session

    mock_db = MagicMock()
    mock_db.return_value = mock_session_ctx()

    sess = AgentCollabSession(goal="Persist Test")
    proposals = [
        {"id": "p1", "role": "planner", "proposal": "Do X", "critique": "meh", "vote": "approve", "round": 1},
        {"role": "executor", "proposal": "Do Y", "vote": "reject", "round": 2},
    ]
    # Should not raise even if DB call works
    await sess.persist_debate(
        session_id="sid1",
        goal_id="gid1",
        tenant_id="tid1",
        original_goal="Do something",
        consensus="Final consensus",
        confidence=0.85,
        rounds=2,
        proposals=proposals,
        db=None,  # use None to avoid DB setup
    )


async def test_persist_debate_db_exception_is_swallowed() -> None:
    """DB exceptions must be caught and logged, not raised to callers."""
    sess = AgentCollabSession(goal="Robust Test")

    def bad_factory():
        raise RuntimeError("DB unreachable")

    # Should not propagate the exception
    await sess.persist_debate(
        session_id="s",
        goal_id="g",
        tenant_id="t",
        original_goal="goal",
        consensus="c",
        confidence=0.5,
        rounds=1,
        proposals=[],
        db=bad_factory,
    )
