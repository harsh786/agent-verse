# tests/memory/test_episodic_procedural.py
"""Episodic and Procedural memory — production-grade DB-backed."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.tenancy.context import TenantContext, PlanTier
from app.memory.episodic import EpisodicMemoryStore, Episode
from app.memory.procedural import ProceduralMemoryStore, Skill


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


def _make_state(tenant_ctx, goal="list open Jira tickets", status=GoalStatus.COMPLETE):
    state = AgentState(goal=goal, tenant_ctx=tenant_ctx, goal_id="g1")
    state.status = status
    step = StepResult(description="search_issues", output="Found 5 tickets", status=StepStatus.COMPLETE)
    step.tool_calls = [{"tool_name": "jira.search_issues", "success": True}]
    state.steps = [step]
    state.verification_feedback = "All tickets found successfully."
    return state


# ── Episodic Memory ───────────────────────────────────────────────────────────

async def test_episodic_record_in_memory(tenant_ctx):
    """record() must store episode in-memory cache."""
    store = EpisodicMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx)
    await store.record(state=state, tenant_ctx=tenant_ctx, quality_score=0.9)
    episodes = await store.recall(goal="Jira tickets", tenant_id="t1")
    assert len(episodes) == 1
    assert episodes[0].outcome == "success"
    assert episodes[0].quality_score == 0.9


async def test_episodic_record_failed_goal(tenant_ctx):
    """Failed goals recorded with outcome='failed'."""
    store = EpisodicMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx, status=GoalStatus.FAILED)
    state.verification_feedback = "Permission denied"
    await store.record(state=state, tenant_ctx=tenant_ctx, quality_score=0.0)
    episodes = await store.recall(goal="list open Jira tickets", tenant_id="t1")
    assert any(e.outcome == "failed" for e in episodes)


async def test_episodic_recall_returns_relevant_episodes(tenant_ctx):
    """recall() returns episodes relevant to the query."""
    store = EpisodicMemoryStore(db_factory=None)
    state1 = _make_state(tenant_ctx, goal="list open Jira tickets")
    state2 = _make_state(tenant_ctx, goal="send Slack message to team")
    state2.steps[0].tool_calls = [{"tool_name": "slack.send_message", "success": True}]
    await store.record(state=state1, tenant_ctx=tenant_ctx, quality_score=0.9)
    await store.record(state=state2, tenant_ctx=tenant_ctx, quality_score=0.8)
    episodes = await store.recall(goal="Jira issues", tenant_id="t1", limit=1)
    # Jira episode should rank higher for a Jira query
    if episodes:
        assert "jira" in episodes[0].goal_text.lower() or "ticket" in episodes[0].goal_text.lower()


async def test_episodic_db_persistence(tenant_ctx):
    """record() must call DB execute when db_factory provided."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock()
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=mock_begin)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin)
    db_factory = MagicMock(return_value=mock_session)

    store = EpisodicMemoryStore(db_factory=db_factory)
    state = _make_state(tenant_ctx)
    await store.record(state=state, tenant_ctx=tenant_ctx)
    assert mock_session.execute.called


def test_episode_context_snippet():
    ep = Episode(
        episode_id="e1", tenant_id="t1", goal_id="g1",
        goal_text="list all open Jira tickets",
        action_summary="searched Jira → found 5 tickets",
        outcome="success",
        lessons="Search with status=Open filter for efficiency",
        quality_score=0.9,
    )
    snippet = ep.to_context_snippet()
    assert "success" in snippet
    assert "Jira" in snippet


# ── Procedural Memory ─────────────────────────────────────────────────────────

async def test_procedural_learn_extracts_tool_sequence(tenant_ctx):
    """learn() extracts tool sequence from goal steps."""
    store = ProceduralMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx)
    await store.learn(state=state, tenant_ctx=tenant_ctx, success=True)
    skills = await store.recall(goal="list Jira tickets", tenant_id="t1")
    assert len(skills) == 1
    assert "jira.search_issues" in skills[0].tool_sequence


async def test_procedural_skill_updates_on_repeat(tenant_ctx):
    """Repeated success increments use_count."""
    store = ProceduralMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx)
    await store.learn(state=state, tenant_ctx=tenant_ctx, success=True)
    await store.learn(state=state, tenant_ctx=tenant_ctx, success=True)
    skills = await store.recall(goal="Jira", tenant_id="t1")
    assert skills[0].use_count == 2


async def test_procedural_skill_updates_success_rate(tenant_ctx):
    """Mixed success/failure updates success_rate correctly."""
    store = ProceduralMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx)
    await store.learn(state=state, tenant_ctx=tenant_ctx, success=True)
    await store.learn(state=state, tenant_ctx=tenant_ctx, success=False)
    skills = await store.recall(goal="Jira", tenant_id="t1", min_success_rate=0.0)
    assert abs(skills[0].success_rate - 0.5) < 0.01


async def test_procedural_db_persistence(tenant_ctx):
    """learn() must call DB execute when db_factory provided."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock()
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=mock_begin)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin)
    db_factory = MagicMock(return_value=mock_session)

    store = ProceduralMemoryStore(db_factory=db_factory)
    state = _make_state(tenant_ctx)
    await store.learn(state=state, tenant_ctx=tenant_ctx)
    assert mock_session.execute.called


def test_skill_hint_format():
    skill = Skill(
        skill_id="s1", tenant_id="t1",
        goal_pattern="list open TICKET in project",
        domain="jira",
        tool_sequence=["jira.search_issues", "jira.get_issue"],
        use_count=5,
        success_rate=0.95,
    )
    hint = skill.to_hint()
    assert "jira.search_issues" in hint
    assert "95%" in hint or "0.95" in hint or "95" in hint
