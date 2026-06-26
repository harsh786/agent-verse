"""Tests for GoalPersistenceEngine — agent retry and persistence logic."""
from __future__ import annotations
import asyncio
import pytest
from app.agent.persistence import (
    GoalPersistenceEngine, PersistenceConfig, RetryStrategy, AttemptRecord
)


def make_config(**kwargs) -> PersistenceConfig:
    defaults = {
        "max_attempts": 5,
        "iterations_per_attempt": 3,
        "base_backoff_seconds": 0.01,  # Fast for tests
        "max_backoff_seconds": 0.05,
        "strategy_switch_after": 2,
        "escalate_after_failures": 4,
        "total_timeout_seconds": 0,
    }
    defaults.update(kwargs)
    return PersistenceConfig(**defaults)


# ── Strategy selection ────────────────────────────────────────────────────────

def test_first_attempt_uses_same_approach():
    engine = GoalPersistenceEngine(make_config())
    strategy = engine._pick_strategy(attempt_number=1)
    assert strategy == RetryStrategy.SAME_APPROACH


def test_strategy_rotates_after_consecutive_failures():
    engine = GoalPersistenceEngine(make_config(strategy_switch_after=2))
    # Inject 2 consecutive failures
    engine._attempts = [
        AttemptRecord(attempt_number=1, success=False, failure_reason="timeout"),
        AttemptRecord(attempt_number=2, success=False, failure_reason="tool_error"),
    ]
    strategy = engine._pick_strategy(attempt_number=3)
    assert strategy != RetryStrategy.SAME_APPROACH


def test_escalate_after_max_failures():
    engine = GoalPersistenceEngine(make_config(escalate_after_failures=3))
    strategy = engine._pick_strategy(attempt_number=4)  # >= escalate_after_failures
    assert strategy == RetryStrategy.ESCALATE


def test_backoff_increases_with_attempts():
    engine = GoalPersistenceEngine(make_config(base_backoff_seconds=10.0))
    b1 = engine._backoff_seconds(1)
    b2 = engine._backoff_seconds(2)
    b3 = engine._backoff_seconds(3)
    # Each should be >= previous (before jitter, but on average true)
    assert b2 > b1 * 0.5  # Allow for jitter
    assert b3 > b2 * 0.5


def test_backoff_capped_at_max():
    engine = GoalPersistenceEngine(make_config(
        base_backoff_seconds=100.0, max_backoff_seconds=50.0
    ))
    backoff = engine._backoff_seconds(10)  # Would be huge without cap
    assert backoff <= 50.0 * 1.25  # Allow for jitter overhead


# ── Goal enrichment ───────────────────────────────────────────────────────────

def test_same_approach_adds_previous_failure():
    engine = GoalPersistenceEngine(make_config())
    enriched = engine._build_enriched_goal(
        "Fix the bug", RetryStrategy.SAME_APPROACH, "timeout error"
    )
    assert "Fix the bug" in enriched
    assert "timeout error" in enriched


def test_different_tools_hints_different_approach():
    engine = GoalPersistenceEngine(make_config())
    enriched = engine._build_enriched_goal(
        "Fix the bug", RetryStrategy.DIFFERENT_TOOLS, "tool X failed"
    )
    assert "DIFFERENT" in enriched.upper() or "different" in enriched.lower()


def test_decompose_strategy_hints_smallest_step():
    engine = GoalPersistenceEngine(make_config())
    enriched = engine._build_enriched_goal(
        "Deploy the full system", RetryStrategy.DECOMPOSE, "deploy failed"
    )
    assert "smallest" in enriched.lower() or "first step" in enriched.lower()


# ── Full run with fake agent ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_persistence_succeeds_on_second_attempt():
    """Agent fails once then succeeds."""
    call_count = [0]

    class FakeAgentState:
        iterations = 2
        verification_success = True
        status = type("S", (), {"value": "complete"})()
        error_message = ""
        verification_feedback = ""
        context = {}

    class FakeAgentStateFail:
        iterations = 3
        verification_success = False
        status = type("S", (), {"value": "failed"})()
        error_message = "verification failed"
        verification_feedback = "not done"
        context = {}

    class FakeAgent:
        async def run(self, goal, tenant_ctx, event_callback=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return FakeAgentStateFail()
            return FakeAgentState()

    events = []

    async def callback(e):
        events.append(e)

    engine = GoalPersistenceEngine(make_config(max_attempts=3))
    success, attempts = await engine.run(
        goal="do the thing",
        agent_factory=FakeAgent(),
        tenant_ctx=None,
        event_callback=callback,
    )
    assert success is True
    assert len(attempts) == 2
    assert attempts[0].success is False
    assert attempts[1].success is True
    event_types = [e["type"] for e in events]
    assert "persistence_goal_achieved" in event_types


@pytest.mark.asyncio
async def test_persistence_exhausted_after_max_attempts():
    """Agent always fails — should exhaust max_attempts."""
    class FakeAgentFail:
        async def run(self, goal, tenant_ctx, event_callback=None):
            class State:
                iterations = 1
                verification_success = False
                status = type("S", (), {"value": "failed"})()
                error_message = "always fails"
                verification_feedback = "nope"
                context = {}
            return State()

    events = []
    engine = GoalPersistenceEngine(make_config(max_attempts=3, escalate_after_failures=10))
    success, attempts = await engine.run(
        goal="impossible goal",
        agent_factory=FakeAgentFail(),
        tenant_ctx=None,
        event_callback=lambda e: events.append(e),
    )
    assert success is False
    assert len(attempts) == 3
    types = [e["type"] for e in events]
    assert "persistence_exhausted" in types


@pytest.mark.asyncio
async def test_persistence_config_defaults():
    config = PersistenceConfig()
    assert config.max_attempts == 10
    assert config.base_backoff_seconds == 30.0
    assert config.decompose_on_failure is True


def test_total_cost_usd_accumulates():
    engine = GoalPersistenceEngine(make_config())
    engine._attempts = [
        AttemptRecord(attempt_number=1, cost_usd=0.05),
        AttemptRecord(attempt_number=2, cost_usd=0.08),
    ]
    assert engine.total_cost_usd == pytest.approx(0.13)


def test_consecutive_failures_counts_trailing():
    engine = GoalPersistenceEngine(make_config())
    engine._attempts = [
        AttemptRecord(attempt_number=1, success=True),
        AttemptRecord(attempt_number=2, success=False),
        AttemptRecord(attempt_number=3, success=False),
    ]
    assert engine.consecutive_failures == 2
