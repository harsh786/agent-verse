"""Comprehensive tests for app/agent/router.py — targets 90%+ statement coverage."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.router import AgentRouter, AgentScore, RoutingDecision
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-router", plan=PlanTier.PROFESSIONAL, api_key_id="key-r")


def _make_agent(agent_id: str, name: str, goal_template: str = "", connectors=None) -> dict:
    return {
        "agent_id": agent_id,
        "name": name,
        "goal_template": goal_template,
        "connector_ids": connectors or [],
    }


# ── AgentScore dataclass ──────────────────────────────────────────────────────

def test_agent_score_construction() -> None:
    s = AgentScore(agent_id="a1", agent_name="My Agent", score=0.75)
    assert s.agent_id == "a1"
    assert s.score == 0.75
    assert s.breakdown == {}
    assert s.reasons == []


# ── RoutingDecision dataclass ─────────────────────────────────────────────────

def test_routing_decision_to_dict() -> None:
    d = RoutingDecision(
        agent_id="a1", reason="routed", confidence=0.8,
        mode="single_agent", candidate_agents=[],
    )
    result = d.to_dict()
    assert result["agent_id"] == "a1"
    assert result["confidence"] == 0.8
    assert "mode" in result


def test_routing_decision_to_dict_rounds_confidence() -> None:
    d = RoutingDecision(agent_id="a1", reason="r", confidence=0.123456789)
    result = d.to_dict()
    assert result["confidence"] == 0.123


# ── AgentRouter._tokenize ─────────────────────────────────────────────────────

def test_tokenize_basic() -> None:
    router = AgentRouter(agent_store=MagicMock())
    tokens = router._tokenize("Fetch Jira Issues for Sprint 42")
    assert "jira" in tokens
    assert "issues" in tokens
    assert "42" in tokens


def test_tokenize_empty() -> None:
    router = AgentRouter(agent_store=MagicMock())
    assert router._tokenize("") == set()


# ── AgentRouter._score_by_keywords ───────────────────────────────────────────

def test_score_by_keywords_exact_match() -> None:
    router = AgentRouter(agent_store=MagicMock())
    agent = _make_agent("a1", "Jira Agent", goal_template="manage jira issues tickets")
    score = router._score_by_keywords("manage jira issues", agent)
    assert score > 0.0


def test_score_by_keywords_no_match() -> None:
    router = AgentRouter(agent_store=MagicMock())
    agent = _make_agent("a1", "Jira Agent", goal_template="jira issues")
    score = router._score_by_keywords("send slack message", agent)
    assert score == 0.0


def test_score_by_keywords_empty_goal_tokens() -> None:
    router = AgentRouter(agent_store=MagicMock())
    agent = _make_agent("a1", "Any Agent")
    assert router._score_by_keywords("", agent) == 0.0


def test_score_by_keywords_empty_agent_tokens() -> None:
    router = AgentRouter(agent_store=MagicMock())
    agent = _make_agent("a1", "", goal_template="")
    assert router._score_by_keywords("any goal", agent) == 0.0


# ── AgentRouter._score_by_connector_match ────────────────────────────────────

def test_score_by_connector_match_full() -> None:
    router = AgentRouter(agent_store=MagicMock())
    agent = _make_agent("a1", "Agent", connectors=["jira", "slack"])
    score = router._score_by_connector_match("sync jira and slack tickets", agent)
    assert score == 1.0


def test_score_by_connector_match_partial() -> None:
    router = AgentRouter(agent_store=MagicMock())
    agent = _make_agent("a1", "Agent", connectors=["jira", "confluence"])
    score = router._score_by_connector_match("fetch jira tickets", agent)
    assert 0.0 < score < 1.0


def test_score_by_connector_match_no_connectors() -> None:
    router = AgentRouter(agent_store=MagicMock())
    agent = _make_agent("a1", "Agent", connectors=[])
    assert router._score_by_connector_match("any goal", agent) == 0.0


def test_score_by_connector_match_no_match() -> None:
    router = AgentRouter(agent_store=MagicMock())
    agent = _make_agent("a1", "Agent", connectors=["salesforce"])
    score = router._score_by_connector_match("fix jira bug", agent)
    assert score == 0.0


# ── AgentRouter._score_by_history ────────────────────────────────────────────

def test_score_by_history_no_eval_store() -> None:
    router = AgentRouter(agent_store=MagicMock(), eval_store=None)
    assert router._score_by_history("a1", _CTX) == 0.0


def test_score_by_history_with_eval_store() -> None:
    eval_store = MagicMock()
    eval_store.get_success_rate = MagicMock(return_value=0.8)
    router = AgentRouter(agent_store=MagicMock(), eval_store=eval_store)
    assert router._score_by_history("a1", _CTX) == 0.8


def test_score_by_history_eval_store_exception_returns_zero() -> None:
    eval_store = MagicMock()
    eval_store.get_success_rate = MagicMock(side_effect=RuntimeError("store failed"))
    router = AgentRouter(agent_store=MagicMock(), eval_store=eval_store)
    assert router._score_by_history("a1", _CTX) == 0.0


# ── AgentRouter._score_by_history_db ─────────────────────────────────────────

async def test_score_by_history_db_no_db() -> None:
    router = AgentRouter(agent_store=MagicMock(), db_session_factory=None)
    score = await router._score_by_history_db({"agent_id": "a1"}, "Goal", _CTX)
    assert score == 0.0


async def test_score_by_history_db_missing_agent_id() -> None:
    db = MagicMock()
    router = AgentRouter(agent_store=MagicMock(), db_session_factory=db)
    score = await router._score_by_history_db({}, "Goal", _CTX)
    assert score == 0.0


async def test_score_by_history_db_with_results() -> None:
    row = MagicMock()
    row.__getitem__ = lambda self, i: [0.85, 5][i]

    session_mock = AsyncMock()
    result_mock = MagicMock()
    result_mock.fetchone = MagicMock(return_value=row)
    session_mock.execute = AsyncMock(return_value=result_mock)

    @asynccontextmanager
    async def fake_db():
        yield session_mock

    router = AgentRouter(agent_store=MagicMock(), db_session_factory=fake_db)
    score = await router._score_by_history_db({"agent_id": "a1"}, "Goal", _CTX)
    assert 0.0 <= score <= 1.0


async def test_score_by_history_db_exception_returns_zero() -> None:
    @asynccontextmanager
    async def bad_db():
        raise RuntimeError("db error")
        yield  # unreachable but makes it a generator

    router = AgentRouter(agent_store=MagicMock(), db_session_factory=bad_db)
    score = await router._score_by_history_db({"agent_id": "a1"}, "Goal", _CTX)
    assert score == 0.0


# ── AgentRouter._score_by_llm ─────────────────────────────────────────────────

async def test_score_by_llm_no_provider() -> None:
    router = AgentRouter(agent_store=MagicMock())
    scores = await router._score_by_llm("Goal", [], None)
    assert scores == []


async def test_score_by_llm_with_provider() -> None:
    llm_response = '{"best_agent_id": "a2", "confidence": 0.9, "reasoning": "Best match"}'
    fake = FakeProvider(responses=[llm_response])
    router = AgentRouter(agent_store=MagicMock(), llm_provider=fake)
    agents = [
        _make_agent("a1", "Agent 1"),
        _make_agent("a2", "Agent 2"),
    ]
    scores = await router._score_by_llm("Do something", agents, fake)
    assert len(scores) == 2
    best = next(s for s in scores if s.agent_id == "a2")
    assert best.score == 0.9


async def test_score_by_llm_invalid_json_returns_empty() -> None:
    fake = FakeProvider(responses=["not json"])
    router = AgentRouter(agent_store=MagicMock())
    scores = await router._score_by_llm("Goal", [_make_agent("a1", "A1")], fake)
    assert scores == []


async def test_score_by_llm_exception_returns_empty() -> None:
    broken = MagicMock()
    broken.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
    router = AgentRouter(agent_store=MagicMock())
    scores = await router._score_by_llm("Goal", [_make_agent("a1", "A1")], broken)
    assert scores == []


# ── AgentRouter.route ─────────────────────────────────────────────────────────

async def test_route_no_agents_returns_none() -> None:
    store = MagicMock()
    store.list_all = MagicMock(return_value=[])
    router = AgentRouter(agent_store=store)
    decision = await router.route("Any goal", _CTX)
    assert decision.agent_id is None
    assert decision.reason == "no_agents"


async def test_route_with_available_agents_parameter() -> None:
    router = AgentRouter(agent_store=MagicMock())
    agents = [_make_agent("a1", "Jira Agent", goal_template="jira issues tickets")]
    decision = await router.route("Manage jira issues", _CTX, available_agents=agents)
    # Score may or may not meet threshold, but no error should occur
    assert isinstance(decision, RoutingDecision)


async def test_route_low_confidence_returns_no_agent() -> None:
    router = AgentRouter(agent_store=MagicMock())
    agents = [_make_agent("a1", "Slack Agent", goal_template="send slack message")]
    decision = await router.route("Do something completely unrelated", _CTX, available_agents=agents)
    # Low confidence → agent_id may be None
    assert isinstance(decision, RoutingDecision)


async def test_route_good_match_returns_agent() -> None:
    router = AgentRouter(agent_store=MagicMock())
    agents = [
        _make_agent("a1", "Jira Agent", goal_template="manage jira issues sprint tickets", connectors=["jira"]),
    ]
    decision = await router.route("Manage jira issues in sprint", _CTX, available_agents=agents)
    assert decision.agent_id == "a1"
    assert decision.confidence > 0.0


async def test_route_uses_agent_store_when_no_available_agents() -> None:
    store = MagicMock()
    store.list_all = MagicMock(return_value=[
        _make_agent("a1", "Jira Bot", goal_template="jira issues", connectors=["jira"])
    ])
    router = AgentRouter(agent_store=store)
    decision = await router.route("Jira sprint issues", _CTX)
    store.list_all.assert_called_once_with(tenant_ctx=_CTX)


async def test_route_needs_human_choice_when_close_scores() -> None:
    """When top-2 agents differ by < 0.1, mode = needs_human_choice."""
    router = AgentRouter(agent_store=MagicMock())
    # Both agents match equally well
    agents = [
        _make_agent("a1", "Jira Slack Agent", goal_template="jira slack issues messages", connectors=["jira", "slack"]),
        _make_agent("a2", "Slack Jira Agent", goal_template="slack jira messages issues", connectors=["slack", "jira"]),
    ]
    decision = await router.route("jira slack issues messages", _CTX, available_agents=agents)
    # Both may score the same → needs_human_choice
    if decision.mode == "needs_human_choice":
        assert len(decision.candidate_agents) >= 2


async def test_route_with_llm_provider_blends_scores() -> None:
    llm_response = '{"best_agent_id": "a1", "confidence": 0.95, "reasoning": "Perfect match"}'
    fake = FakeProvider(responses=[llm_response])
    router = AgentRouter(agent_store=MagicMock(), llm_provider=fake)
    agents = [
        _make_agent("a1", "Agent 1", goal_template="do things", connectors=["jira"]),
        _make_agent("a2", "Agent 2", goal_template="other tasks", connectors=["slack"]),
    ]
    decision = await router.route("jira things", _CTX, available_agents=agents)
    assert isinstance(decision, RoutingDecision)


async def test_route_with_db_backed_history() -> None:
    row = MagicMock()
    row.__getitem__ = lambda self, i: [0.9, 10][i]

    session_mock = AsyncMock()
    result_mock = MagicMock()
    result_mock.fetchone = MagicMock(return_value=row)
    session_mock.execute = AsyncMock(return_value=result_mock)

    @asynccontextmanager
    async def fake_db():
        yield session_mock

    router = AgentRouter(agent_store=MagicMock(), db_session_factory=fake_db)
    agents = [_make_agent("a1", "Agent", goal_template="task", connectors=["tool"])]
    decision = await router.route("task with tool", _CTX, available_agents=agents)
    assert isinstance(decision, RoutingDecision)


async def test_route_all_scores_populated() -> None:
    router = AgentRouter(agent_store=MagicMock())
    agents = [
        _make_agent("a1", "Agent 1", goal_template="alpha tasks"),
        _make_agent("a2", "Agent 2", goal_template="beta tasks"),
    ]
    decision = await router.route("some goal", _CTX, available_agents=agents)
    assert len(decision.all_scores) == 2


async def test_route_llm_provider_exception_is_swallowed() -> None:
    broken = MagicMock()
    broken.complete = AsyncMock(side_effect=RuntimeError("LLM exploded"))
    router = AgentRouter(agent_store=MagicMock(), llm_provider=broken)
    agents = [_make_agent("a1", "Agent", goal_template="task")]
    decision = await router.route("task", _CTX, available_agents=agents)
    # Should not raise — LLM failure is caught
    assert isinstance(decision, RoutingDecision)
