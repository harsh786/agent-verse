# tests/memory/test_episodic_procedural.py
"""Episodic and Procedural memory — production-grade DB-backed."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.memory.episodic import Episode, EpisodicMemoryStore
from app.memory.procedural import ProceduralMemoryStore, Skill
from app.tenancy.context import PlanTier, TenantContext


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


async def test_episodic_cache_evicts_oldest_past_100_per_tenant(tenant_ctx):
    """The in-memory cache is capped at 100 episodes per tenant (oldest evicted first)."""
    store = EpisodicMemoryStore(db_factory=None)
    for i in range(105):
        state = _make_state(tenant_ctx, goal=f"goal number {i}")
        await store.record(state=state, tenant_ctx=tenant_ctx, quality_score=0.5)

    cached = store._cache["t1"]
    assert len(cached) == 100
    goal_texts = {ep.goal_text for ep in cached}
    # The first 5 recorded episodes must have been evicted.
    for i in range(5):
        assert f"goal number {i}" not in goal_texts
    # The most recent ones must survive.
    assert "goal number 104" in goal_texts


async def test_episodic_recall_ordering_prefers_higher_keyword_relevance(tenant_ctx):
    """recall() (in-memory path) ranks episodes by keyword overlap, best match first."""
    store = EpisodicMemoryStore(db_factory=None)
    low = _make_state(tenant_ctx, goal="send an email")
    high = _make_state(tenant_ctx, goal="search jira issues for open tickets")
    await store.record(state=low, tenant_ctx=tenant_ctx, quality_score=0.5)
    await store.record(state=high, tenant_ctx=tenant_ctx, quality_score=0.5)

    episodes = await store.recall(goal="search jira open tickets", tenant_id="t1", limit=2)
    assert episodes[0].goal_text == "search jira issues for open tickets"


async def test_episodic_recall_db_ordering_prefers_relevance_then_quality(tenant_ctx):
    """_recall_from_db must rank by keyword relevance first, quality_score as tiebreak."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.fetchall = MagicMock(
        return_value=[
            ("e1", "g1", "unrelated goal about email", "summary", "success", "", 0.9, 1, "[]"),
            ("e2", "g2", "jira ticket search for open issues", "summary", "success", "", 0.4, 1, "[]"),
        ]
    )
    mock_session.execute = AsyncMock(return_value=mock_result)
    db_factory = MagicMock(return_value=mock_session)

    store = EpisodicMemoryStore(db_factory=db_factory)
    episodes = await store.recall(goal="jira ticket search", tenant_id="t1", limit=2)
    assert episodes[0].episode_id == "e2"


async def test_episodic_goal_text_and_lessons_are_truncated(tenant_ctx):
    """Overlong goal/lesson text is truncated at write time (200 / 300 chars)."""
    store = EpisodicMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx, goal="g" * 500)
    state.verification_feedback = "l" * 500
    await store.record(state=state, tenant_ctx=tenant_ctx)
    episodes = await store.recall(goal="g" * 500, tenant_id="t1")
    assert len(episodes[0].goal_text) == 200
    assert len(episodes[0].lessons) == 300


async def test_episodic_action_summary_uses_only_first_five_steps(tenant_ctx):
    store = EpisodicMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx)
    state.steps = [
        StepResult(description=f"step-{i}", output="ok", status=StepStatus.COMPLETE)
        for i in range(8)
    ]
    await store.record(state=state, tenant_ctx=tenant_ctx)
    episodes = await store.recall(goal=state.goal, tenant_id="t1")
    assert "step-5" not in episodes[0].action_summary
    assert "step-4" in episodes[0].action_summary


async def test_episodic_no_steps_records_placeholder_summary(tenant_ctx):
    store = EpisodicMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx)
    state.steps = []
    await store.record(state=state, tenant_ctx=tenant_ctx)
    episodes = await store.recall(goal=state.goal, tenant_id="t1")
    assert episodes[0].action_summary == "No steps recorded"
    assert episodes[0].steps_count == 0


async def test_episodic_tools_used_deduplicated_and_capped_at_ten(tenant_ctx):
    store = EpisodicMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx)
    step = state.steps[0]
    step.tool_calls = [{"tool_name": f"tool.{i % 12}", "success": True} for i in range(30)]
    await store.record(state=state, tenant_ctx=tenant_ctx)
    episodes = await store.recall(goal=state.goal, tenant_id="t1")
    assert len(episodes[0].tools_used) == 10
    assert len(set(episodes[0].tools_used)) == len(episodes[0].tools_used)


async def test_episodic_recall_db_failure_falls_back_to_in_memory_cache(tenant_ctx):
    """A DB error on recall must not raise — falls back to the in-memory cache."""

    def _boom() -> None:
        raise RuntimeError("db connection refused")

    store = EpisodicMemoryStore(db_factory=MagicMock(side_effect=_boom))
    # record() also tries the (failing) db_factory but must still cache in-memory.
    state = _make_state(tenant_ctx)
    await store.record(state=state, tenant_ctx=tenant_ctx)

    episodes = await store.recall(goal=state.goal, tenant_id="t1")
    assert len(episodes) == 1
    assert episodes[0].outcome == "success"


async def test_episodic_recall_db_malformed_tools_used_json_does_not_raise(tenant_ctx):
    """A corrupted tools_used JSONB value must not crash recall (falls back gracefully)."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.fetchall = MagicMock(
        return_value=[("e1", "g1", "goal text", "summary", "success", "", 0.5, 1, "{not valid json")]
    )
    mock_session.execute = AsyncMock(return_value=mock_result)
    db_factory = MagicMock(return_value=mock_session)

    store = EpisodicMemoryStore(db_factory=db_factory)
    # No exception should propagate; falls back to the (empty) in-memory cache.
    episodes = await store.recall(goal="goal text", tenant_id="t1")
    assert episodes == []


def test_episodic_memory_store_has_no_ttl_or_purge_mechanism(tenant_ctx):
    """FINDING: EpisodicMemoryStore has no TTL/expiry or purge path (unlike canonical
    memory's retention.py). The episodic_memories table (migration 0089) has no
    expires_at column and no scheduled purge task references it — records grow
    unbounded in Postgres even though the in-memory cache is capped at 100/tenant.
    This test documents the absence rather than asserting behavior that doesn't
    exist; see task report for the flagged finding.
    """
    store = EpisodicMemoryStore(db_factory=None)
    assert not hasattr(store, "purge_expired")
    assert not hasattr(Episode, "expires_at")


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


async def test_procedural_learn_skips_when_no_tool_calls(tenant_ctx):
    """A goal execution with no tool_calls teaches nothing (nothing to reuse)."""
    store = ProceduralMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx)
    state.steps[0].tool_calls = []
    await store.learn(state=state, tenant_ctx=tenant_ctx, success=True)
    skills = await store.recall(goal="list Jira tickets", tenant_id="t1", min_success_rate=0.0)
    assert skills == []


async def test_procedural_tool_sequence_capped_at_eight(tenant_ctx):
    store = ProceduralMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx)
    state.steps[0].tool_calls = [{"tool_name": f"tool.{i}", "success": True} for i in range(12)]
    await store.learn(state=state, tenant_ctx=tenant_ctx, success=True)
    skills = await store.recall(goal=state.goal, tenant_id="t1", min_success_rate=0.0)
    assert len(skills[0].tool_sequence) == 8
    assert skills[0].tool_sequence == [f"tool.{i}" for i in range(8)]


async def test_procedural_goal_pattern_normalization_merges_similar_goals(tenant_ctx):
    """Ticket IDs / numbers / quoted values are generalized so near-duplicate goals share a skill."""
    store = ProceduralMemoryStore(db_factory=None)
    state1 = _make_state(tenant_ctx, goal='Update PROJ-123 to status "Open" for 5 users')
    state2 = _make_state(tenant_ctx, goal='Update PROJ-456 to status "Open" for 3 users')
    await store.learn(state=state1, tenant_ctx=tenant_ctx, success=True)
    await store.learn(state=state2, tenant_ctx=tenant_ctx, success=True)

    skills = await store.recall(goal="Update ticket status", tenant_id="t1", min_success_rate=0.0)
    assert len(skills) == 1
    assert skills[0].use_count == 2
    assert "TICKET" in skills[0].goal_pattern
    assert "VALUE" in skills[0].goal_pattern
    assert "N" in skills[0].goal_pattern


@pytest.mark.parametrize(
    ("tool_name", "expected_domain"),
    [
        ("jira.search_issues", "jira"),
        ("github.create_pr", "git"),
        ("gitlab.merge_request", "git"),
        ("postgres.query", "database"),
        ("sql.execute", "database"),
        ("slack.send_message", "communication"),
        ("email.send", "communication"),
        ("web.fetch", "web"),
        ("browser.click", "web"),
        ("custom.unrecognized_tool", "general"),
    ],
)
async def test_procedural_domain_inference(tenant_ctx, tool_name, expected_domain):
    store = ProceduralMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx, goal=f"do something with {tool_name}")
    state.steps[0].tool_calls = [{"tool_name": tool_name, "success": True}]
    await store.learn(state=state, tenant_ctx=tenant_ctx, success=True)
    skills = await store.recall(goal=state.goal, tenant_id="t1", min_success_rate=0.0)
    assert skills[0].domain == expected_domain


async def test_procedural_recall_excludes_low_success_rate_skills_by_default(tenant_ctx):
    store = ProceduralMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx)
    for _ in range(3):
        await store.learn(state=state, tenant_ctx=tenant_ctx, success=False)
    # Default min_success_rate=0.6 excludes a skill with a 0% success rate.
    skills = await store.recall(goal="Jira", tenant_id="t1")
    assert skills == []
    # Explicitly lowering the bar surfaces it again.
    skills = await store.recall(goal="Jira", tenant_id="t1", min_success_rate=0.0)
    assert len(skills) == 1


async def test_procedural_recall_domain_filter_excludes_other_domains(tenant_ctx):
    store = ProceduralMemoryStore(db_factory=None)
    jira_state = _make_state(tenant_ctx, goal="list Jira tickets")
    github_state = _make_state(tenant_ctx, goal="open a github pull request")
    github_state.steps[0].tool_calls = [{"tool_name": "github.create_pr", "success": True}]
    await store.learn(state=jira_state, tenant_ctx=tenant_ctx, success=True)
    await store.learn(state=github_state, tenant_ctx=tenant_ctx, success=True)

    jira_only = await store.recall(
        goal="tickets", tenant_id="t1", domain="jira", min_success_rate=0.0
    )
    assert len(jira_only) == 1
    assert jira_only[0].domain == "jira"


async def test_procedural_recall_db_failure_falls_back_to_in_memory_cache(tenant_ctx):
    def _boom() -> None:
        raise RuntimeError("db connection refused")

    store = ProceduralMemoryStore(db_factory=MagicMock(side_effect=_boom))
    state = _make_state(tenant_ctx)
    await store.learn(state=state, tenant_ctx=tenant_ctx, success=True)

    skills = await store.recall(goal=state.goal, tenant_id="t1", min_success_rate=0.0)
    assert len(skills) == 1
    assert "jira.search_issues" in skills[0].tool_sequence


async def test_procedural_recall_db_malformed_tool_sequence_json_does_not_raise(tenant_ctx):
    """A corrupted tool_sequence JSONB value must not crash recall."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.fetchall = MagicMock(
        return_value=[("s1", "goal pattern", "jira", "{not valid json", 1, 0.9)]
    )
    mock_session.execute = AsyncMock(return_value=mock_result)
    db_factory = MagicMock(return_value=mock_session)

    store = ProceduralMemoryStore(db_factory=db_factory)
    skills = await store.recall(goal="goal pattern", tenant_id="t1", min_success_rate=0.0)
    assert skills == []


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
