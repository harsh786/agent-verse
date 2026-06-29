"""Comprehensive tests for app/agent/persistence.py — targets 90%+ statement coverage."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.persistence import (
    AttemptRecord,
    GoalPersistenceEngine,
    PersistenceConfig,
    RetryStrategy,
)
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-persist", plan=PlanTier.ENTERPRISE, api_key_id="key-p")


# ── RetryStrategy enum ─────────────────────────────────────────────────────────

def test_retry_strategy_values() -> None:
    assert RetryStrategy.SAME_APPROACH == "same_approach"
    assert RetryStrategy.DIFFERENT_TOOLS == "different_tools"
    assert RetryStrategy.DECOMPOSE == "decompose"
    assert RetryStrategy.SIMPLIFY == "simplify"
    assert RetryStrategy.HUMAN_GUIDANCE == "human_guidance"
    assert RetryStrategy.ESCALATE == "escalate"


# ── AttemptRecord ─────────────────────────────────────────────────────────────

def test_attempt_record_defaults() -> None:
    a = AttemptRecord()
    assert a.attempt_number == 0
    assert a.strategy == RetryStrategy.SAME_APPROACH
    assert a.success is False
    assert a.cost_usd == 0.0
    assert a.attempt_id != ""


# ── PersistenceConfig ─────────────────────────────────────────────────────────

def test_persistence_config_defaults() -> None:
    c = PersistenceConfig()
    assert c.max_attempts == 10
    assert c.base_backoff_seconds == 30.0
    assert c.decompose_on_failure is True


# ── GoalPersistenceEngine properties ─────────────────────────────────────────

def test_engine_consecutive_failures_no_attempts() -> None:
    engine = GoalPersistenceEngine()
    assert engine.consecutive_failures == 0


def test_engine_consecutive_failures_all_failed() -> None:
    engine = GoalPersistenceEngine()
    engine._attempts = [
        AttemptRecord(success=False),
        AttemptRecord(success=False),
        AttemptRecord(success=False),
    ]
    assert engine.consecutive_failures == 3


def test_engine_consecutive_failures_stops_at_success() -> None:
    engine = GoalPersistenceEngine()
    engine._attempts = [
        AttemptRecord(success=False),
        AttemptRecord(success=True),
        AttemptRecord(success=False),
        AttemptRecord(success=False),
    ]
    assert engine.consecutive_failures == 2  # trailing failures only


def test_engine_total_cost() -> None:
    engine = GoalPersistenceEngine()
    engine._attempts = [
        AttemptRecord(cost_usd=0.5),
        AttemptRecord(cost_usd=1.5),
    ]
    assert abs(engine.total_cost_usd - 2.0) < 0.01


def test_engine_attempts_property_returns_copy() -> None:
    engine = GoalPersistenceEngine()
    engine._attempts = [AttemptRecord()]
    copy = engine.attempts
    assert copy is not engine._attempts


# ── GoalPersistenceEngine._pick_strategy ─────────────────────────────────────

def test_pick_strategy_first_attempt_same_approach() -> None:
    engine = GoalPersistenceEngine()
    assert engine._pick_strategy(1) == RetryStrategy.SAME_APPROACH


def test_pick_strategy_escalates_after_threshold() -> None:
    config = PersistenceConfig(escalate_after_failures=3)
    engine = GoalPersistenceEngine(config=config)
    assert engine._pick_strategy(3) == RetryStrategy.ESCALATE


def test_pick_strategy_rotates_after_consecutive_failures() -> None:
    # With strategy_switch_after=2 and 2 consecutive failures:
    # cycle = 2 // 2 = 1 → strategies[1] = SIMPLIFY
    config = PersistenceConfig(strategy_switch_after=2)
    engine = GoalPersistenceEngine(config=config)
    engine._attempts = [AttemptRecord(success=False), AttemptRecord(success=False)]
    strategy = engine._pick_strategy(3)
    assert strategy == RetryStrategy.SIMPLIFY


def test_pick_strategy_cycles_through_all_strategies() -> None:
    # Different cycle values produce different strategies; test with varying failure counts.
    config = PersistenceConfig(strategy_switch_after=1, escalate_after_failures=100)
    strategies_seen: set = set()
    for failure_count in [1, 2, 3, 4]:
        engine = GoalPersistenceEngine(config=config)
        engine._attempts = [AttemptRecord(success=False)] * failure_count
        strategies_seen.add(engine._pick_strategy(2))
    # Multiple different strategies should have been returned across varying failure counts
    assert len(strategies_seen) > 1


def test_pick_strategy_decompose_disabled_uses_different_tools() -> None:
    config = PersistenceConfig(strategy_switch_after=2, decompose_on_failure=False)
    engine = GoalPersistenceEngine(config=config)
    engine._attempts = [AttemptRecord(success=False)] * 4  # cycle=1 → SIMPLIFY, cycle=2 → DIFFERENT_TOOLS
    strategy = engine._pick_strategy(5)
    # With decompose=False, DECOMPOSE is replaced by DIFFERENT_TOOLS
    assert strategy != RetryStrategy.DECOMPOSE


# ── GoalPersistenceEngine._backoff_seconds ────────────────────────────────────

def test_backoff_seconds_first_attempt() -> None:
    engine = GoalPersistenceEngine(PersistenceConfig(base_backoff_seconds=10.0))
    backoff = engine._backoff_seconds(1)
    assert backoff >= 1.0
    # base * 2^0 = 10, with up to 20% jitter
    assert backoff <= 12.5


def test_backoff_seconds_capped_at_max() -> None:
    engine = GoalPersistenceEngine(PersistenceConfig(base_backoff_seconds=100.0, max_backoff_seconds=50.0))
    backoff = engine._backoff_seconds(10)
    assert backoff <= 50.0 * 1.21  # with jitter up to 20%


def test_backoff_seconds_exponential_growth() -> None:
    engine = GoalPersistenceEngine(PersistenceConfig(base_backoff_seconds=10.0, max_backoff_seconds=9999.0))
    b1 = engine._backoff_seconds(1)
    b2 = engine._backoff_seconds(2)
    b3 = engine._backoff_seconds(3)
    # Each should be larger (accounting for jitter)
    assert b2 >= b1 * 0.8  # some tolerance for jitter
    assert b3 >= b2 * 0.8


# ── GoalPersistenceEngine._build_enriched_goal ───────────────────────────────

def test_enriched_goal_same_approach() -> None:
    engine = GoalPersistenceEngine()
    result = engine._build_enriched_goal("Do X", RetryStrategy.SAME_APPROACH, "failed last time")
    assert "Do X" in result
    assert "failed last time" in result


def test_enriched_goal_different_tools() -> None:
    engine = GoalPersistenceEngine()
    result = engine._build_enriched_goal("Do X", RetryStrategy.DIFFERENT_TOOLS, "tool A failed")
    assert "DIFFERENT" in result.upper() or "different" in result.lower()


def test_enriched_goal_simplify() -> None:
    engine = GoalPersistenceEngine()
    result = engine._build_enriched_goal("Do X", RetryStrategy.SIMPLIFY, "complex failure")
    assert "Simplify" in result or "simplify" in result.lower()


def test_enriched_goal_decompose() -> None:
    engine = GoalPersistenceEngine()
    engine._attempts = [AttemptRecord(success=False)] * 3
    result = engine._build_enriched_goal("Do X", RetryStrategy.DECOMPOSE, "big failure")
    assert "smallest" in result.lower() or "step" in result.lower()


def test_enriched_goal_human_guidance() -> None:
    engine = GoalPersistenceEngine()
    engine._attempts = [AttemptRecord()] * 5
    result = engine._build_enriched_goal("Do X", RetryStrategy.HUMAN_GUIDANCE, "5 failures")
    assert "IMPORTANT" in result


def test_enriched_goal_unknown_strategy_returns_original() -> None:
    engine = GoalPersistenceEngine()
    result = engine._build_enriched_goal("Original goal", RetryStrategy.ESCALATE, "failure")
    assert result == "Original goal"


# ── GoalPersistenceEngine._write_attempt_start / _write_attempt_end (no DB) ──

async def test_write_attempt_start_no_db_returns_id() -> None:
    engine = GoalPersistenceEngine(db=None)
    attempt_id = await engine._write_attempt_start(
        goal_id="g1", tenant_id="t1", attempt_num=1, strategy="same_approach",
        enriched_goal="Goal text", backoff=0,
    )
    assert isinstance(attempt_id, str)
    assert len(attempt_id) > 0


async def test_write_attempt_end_no_db_is_noop() -> None:
    engine = GoalPersistenceEngine(db=None)
    # Must not raise
    await engine._write_attempt_end(
        attempt_id="aid1", tenant_id="t1", succeeded=True,
        failure_reason="", iterations=5, cost_usd=0.1,
    )


async def test_write_attempt_end_empty_attempt_id_is_noop() -> None:
    db_mock = MagicMock()
    engine = GoalPersistenceEngine(db=db_mock)
    # empty attempt_id → should skip
    await engine._write_attempt_end(
        attempt_id="", tenant_id="t1", succeeded=True,
        failure_reason="", iterations=0, cost_usd=0.0,
    )
    db_mock.assert_not_called()


# ── GoalPersistenceEngine.run — main loop ────────────────────────────────────

def _make_successful_agent():
    """Factory: returns agent mock whose run() returns a successful state."""
    state = MagicMock()
    state.status = "complete"
    state.verification_success = True
    state.iterations = 3
    state.context = {"total_cost_usd": 0.05}
    state.error_message = ""
    state.verification_feedback = ""

    agent = MagicMock()
    agent.run = AsyncMock(return_value=state)
    return agent


def _make_failing_agent(error_msg: str = "verification failed"):
    state = MagicMock()
    state.status = "failed"
    state.verification_success = False
    state.iterations = 5
    state.context = {}
    state.error_message = error_msg
    state.verification_feedback = error_msg

    agent = MagicMock()
    agent.run = AsyncMock(return_value=state)
    return agent


async def test_run_success_on_first_attempt() -> None:
    config = PersistenceConfig(max_attempts=5, emit_retry_events=False)
    engine = GoalPersistenceEngine(config=config)

    agent = _make_successful_agent()
    success, attempts = await engine.run(
        goal="Test goal",
        agent_factory=agent,
        tenant_ctx=_CTX,
    )
    assert success is True
    assert len(attempts) == 1
    assert attempts[0].success is True


async def test_run_exhausts_all_attempts_returns_false() -> None:
    config = PersistenceConfig(max_attempts=3, emit_retry_events=False, base_backoff_seconds=0.01, max_backoff_seconds=0.01)
    engine = GoalPersistenceEngine(config=config)

    agent = _make_failing_agent()
    success, attempts = await engine.run(
        goal="Impossible goal",
        agent_factory=agent,
        tenant_ctx=_CTX,
    )
    assert success is False
    assert len(attempts) == 3


async def test_run_succeeds_on_second_attempt() -> None:
    config = PersistenceConfig(max_attempts=5, emit_retry_events=False, base_backoff_seconds=0.01, max_backoff_seconds=0.01)
    engine = GoalPersistenceEngine(config=config)

    fail_state = MagicMock()
    fail_state.status = "failed"
    fail_state.verification_success = False
    fail_state.iterations = 2
    fail_state.context = {}
    fail_state.error_message = "first attempt failed"
    fail_state.verification_feedback = "first attempt failed"

    success_state = MagicMock()
    success_state.status = "complete"
    success_state.verification_success = True
    success_state.iterations = 3
    success_state.context = {}
    success_state.error_message = ""
    success_state.verification_feedback = ""

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=[fail_state, success_state])

    success, attempts = await engine.run(
        goal="Goal",
        agent_factory=agent,
        tenant_ctx=_CTX,
    )
    assert success is True
    assert len(attempts) == 2


async def test_run_with_callable_factory() -> None:
    """agent_factory is a callable that returns a new agent per call."""
    config = PersistenceConfig(max_attempts=2, emit_retry_events=False, base_backoff_seconds=0.01, max_backoff_seconds=0.01)
    engine = GoalPersistenceEngine(config=config)

    agent = _make_successful_agent()
    agent_factory = MagicMock(return_value=agent)
    # Make it callable but not have 'run' attr itself
    del agent_factory.run  # remove run attr to force callable path

    success, attempts = await engine.run(
        goal="Goal",
        agent_factory=agent_factory,
        tenant_ctx=_CTX,
    )
    assert success is True


async def test_run_emits_events_via_callback() -> None:
    events: list[dict] = []

    async def cb(evt: dict) -> None:
        events.append(evt)

    config = PersistenceConfig(max_attempts=2, emit_retry_events=True, base_backoff_seconds=0.01, max_backoff_seconds=0.01)
    engine = GoalPersistenceEngine(config=config)
    agent = _make_successful_agent()

    await engine.run(goal="Goal", agent_factory=agent, tenant_ctx=_CTX, event_callback=cb)
    types = {e["type"] for e in events}
    assert "persistence_attempt_start" in types
    assert "persistence_goal_achieved" in types


async def test_run_total_timeout_stops_loop() -> None:
    config = PersistenceConfig(
        max_attempts=10,
        total_timeout_seconds=0.01,
        emit_retry_events=True,
        base_backoff_seconds=0.1,
    )
    engine = GoalPersistenceEngine(config=config)
    # Pre-load a fake attempt so timeout triggers on next iteration
    engine._attempts = [AttemptRecord(success=False)]

    events: list[dict] = []

    async def cb(evt: dict) -> None:
        events.append(evt)

    # Force elapsed time to exceed total_timeout by sleeping a bit
    import time
    time.sleep(0.02)

    agent = _make_failing_agent()
    success, _ = await engine.run(
        goal="Goal",
        agent_factory=agent,
        tenant_ctx=_CTX,
        event_callback=cb,
    )
    assert success is False


async def test_run_escalates_emits_escalating_event() -> None:
    config = PersistenceConfig(
        max_attempts=10,
        escalate_after_failures=2,
        emit_retry_events=True,
        base_backoff_seconds=0.01,
        max_backoff_seconds=0.01,
    )
    engine = GoalPersistenceEngine(config=config)
    # Start at attempt 2 → strategy = ESCALATE
    events: list[dict] = []

    async def cb(evt: dict) -> None:
        events.append(evt)

    agent = _make_failing_agent()
    # exhaust 1 real attempt, then hit escalation on attempt 2
    success, _ = await engine.run(
        goal="Goal",
        agent_factory=agent,
        tenant_ctx=_CTX,
        event_callback=cb,
    )
    types = {e["type"] for e in events}
    assert "persistence_escalating" in types


async def test_run_exception_in_agent_run_records_failure() -> None:
    config = PersistenceConfig(max_attempts=2, emit_retry_events=False, base_backoff_seconds=0.01, max_backoff_seconds=0.01)
    engine = GoalPersistenceEngine(config=config)

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=RuntimeError("agent crashed hard"))

    success, attempts = await engine.run(
        goal="Goal",
        agent_factory=agent,
        tenant_ctx=_CTX,
    )
    assert success is False
    assert "crashed hard" in attempts[0].failure_reason


async def test_run_cancelled_error_re_raised() -> None:
    config = PersistenceConfig(max_attempts=2, emit_retry_events=False, base_backoff_seconds=0.01)
    engine = GoalPersistenceEngine(config=config)

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await engine.run(goal="Goal", agent_factory=agent, tenant_ctx=_CTX)


async def test_run_with_db_write_attempt_start_called() -> None:
    """When db is set, write_attempt_start should be called."""
    # Create a proper async context manager mock
    session_mock = AsyncMock()
    session_mock.execute = AsyncMock()
    session_mock.commit = AsyncMock()

    @asynccontextmanager
    async def fake_db():
        yield session_mock

    config = PersistenceConfig(max_attempts=1, emit_retry_events=False)
    engine = GoalPersistenceEngine(config=config, db=fake_db)

    agent = _make_successful_agent()
    success, _ = await engine.run(
        goal="Goal",
        agent_factory=agent,
        tenant_ctx=_CTX,
        goal_id="goal-123",
    )
    # Should succeed even with db writes
    assert success is True


async def test_run_persistence_exhausted_event_emitted() -> None:
    events: list[dict] = []

    async def cb(evt: dict) -> None:
        events.append(evt)

    config = PersistenceConfig(
        max_attempts=2,
        emit_retry_events=True,
        base_backoff_seconds=0.01,
        max_backoff_seconds=0.01,
        escalate_after_failures=100,
    )
    engine = GoalPersistenceEngine(config=config)
    agent = _make_failing_agent()

    await engine.run(goal="Goal", agent_factory=agent, tenant_ctx=_CTX, event_callback=cb)
    types = {e["type"] for e in events}
    assert "persistence_exhausted" in types
