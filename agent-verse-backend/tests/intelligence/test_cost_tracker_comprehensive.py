"""Comprehensive tests for CostTracker — budget status, record_llm_usage,
anomaly detection, cost prediction, per-agent summary, and edge cases.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.intelligence.cost_tracker import (
    MODEL_PRICING,
    BudgetLimits,
    CostAnomaly,
    CostTracker,
    calculate_cost,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_redis(data: dict | None = None) -> AsyncMock:
    store: dict[str, str] = dict(data or {})
    redis = AsyncMock()

    async def _get(key):
        return store.get(key)

    async def _set(key, value, **_kw):
        store[key] = value

    async def _setex(key, _ttl, value):
        store[key] = value

    async def _incrbyfloat(key, amount):
        current = float(store.get(key, "0"))
        store[key] = str(current + amount)
        return current + amount

    async def _expire(key, _ttl):
        return key in store

    async def _scan(cursor, match="*", count=100):
        matched = [k for k in store if _match_pattern(match, k)]
        return 0, matched

    def _match_pattern(pattern, key):
        import fnmatch
        return fnmatch.fnmatch(key, pattern)

    redis.get = AsyncMock(side_effect=_get)
    redis.set = AsyncMock(side_effect=_set)
    redis.setex = AsyncMock(side_effect=_setex)
    redis.incrbyfloat = AsyncMock(side_effect=_incrbyfloat)
    redis.expire = AsyncMock(side_effect=_expire)
    redis.scan = AsyncMock(side_effect=_scan)
    redis.publish = AsyncMock(return_value=0)
    return redis


def _ctx(tid: str = "t1"):
    ctx = MagicMock()
    ctx.tenant_id = tid
    return ctx


# ── 1. calculate_cost ─────────────────────────────────────────────────────────

def test_calculate_cost_exact_known_model():
    # claude-opus-4: input=15.0, output=75.0 per 1M
    cost = calculate_cost("claude-opus-4", 1_000_000, 1_000_000)
    assert cost == pytest.approx(90.0)


def test_calculate_cost_prefix_match():
    # "claude-haiku-3-5-new" should prefix-match "claude-haiku-3-5"
    cost = calculate_cost("claude-haiku-3-5-20241022", 1_000_000, 0)
    expected = MODEL_PRICING["claude-haiku-3-5"]["input"]
    assert cost == pytest.approx(expected)


def test_calculate_cost_unknown_falls_back():
    cost = calculate_cost("totally-unknown-model", 1_000, 0)
    assert cost == pytest.approx(1_000 * 3.0 / 1_000_000)


def test_calculate_cost_zero_tokens():
    assert calculate_cost("gpt-4o", 0, 0) == 0.0


def test_calculate_cost_gemini_flash():
    # gemini-2.0-flash: input=0.075, output=0.30
    cost = calculate_cost("gemini-2.0-flash", 1_000_000, 1_000_000)
    assert cost == pytest.approx(0.375)


# ── 2. CostTracker helpers ────────────────────────────────────────────────────

def test_daily_key_format():
    tracker = CostTracker()
    with patch.object(CostTracker, "_today", return_value="2026-01-15"):
        key = tracker._daily_key("tenant-x")
    assert key == "cost:daily:tenant-x:2026-01-15"


def test_goal_key_format():
    assert CostTracker._goal_key("goal-abc") == "cost:goal:goal-abc"


def test_ewma_key_with_agent():
    assert CostTracker._ewma_key("t1", "agent1") == "cost_ewma:t1:agent1"


def test_ewma_key_without_agent():
    assert CostTracker._ewma_key("t1", None) == "cost_ewma:t1:tenant"


# ── 3. get_budget_status ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_budget_status_no_redis():
    tracker = CostTracker(redis=None)
    status = await tracker.get_budget_status("t1")
    assert status["daily_spent"] == 0.0
    assert status["daily_limit"] == 500.0
    assert status["daily_remaining"] == 500.0
    assert status["budget_pct_remaining"] == 1.0


@pytest.mark.asyncio
async def test_get_budget_status_with_goal():
    redis = _make_redis({
        "cost:daily:t1:2026-06-29": "150.0",
        "cost:goal:g1": "25.0",
    })
    tracker = CostTracker(redis=redis)
    with patch.object(CostTracker, "_today", return_value="2026-06-29"):
        status = await tracker.get_budget_status("t1", goal_id="g1")
    assert status["daily_spent"] == pytest.approx(150.0)
    assert status["goal_spent"] == pytest.approx(25.0)
    assert status["daily_remaining"] == pytest.approx(350.0)


@pytest.mark.asyncio
async def test_get_budget_status_does_not_incrbyfloat():
    redis = _make_redis({"cost:daily:t1:2026-06-29": "10.0"})
    tracker = CostTracker(redis=redis)
    with patch.object(CostTracker, "_today", return_value="2026-06-29"):
        await tracker.get_budget_status("t1")
    redis.incrbyfloat.assert_not_called()


@pytest.mark.asyncio
async def test_get_budget_status_over_limit_remaining_zero():
    redis = _make_redis({"cost:daily:t1:2026-06-29": "600.0"})
    tracker = CostTracker(redis=redis)
    with patch.object(CostTracker, "_today", return_value="2026-06-29"):
        status = await tracker.get_budget_status("t1")
    assert status["daily_remaining"] == 0.0


# ── 4. record_llm_usage ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_llm_usage_no_redis_no_db():
    tracker = CostTracker()
    ctx = _ctx("t1")
    cost = await tracker.record_llm_usage(
        model="gpt-4o-mini",
        prompt_tokens=1000,
        completion_tokens=100,
        tenant_ctx=ctx,
        goal_id="g1",
    )
    assert cost > 0


@pytest.mark.asyncio
async def test_record_llm_usage_increments_redis():
    redis = _make_redis()
    tracker = CostTracker(redis=redis)
    ctx = _ctx("t-test")
    await tracker.record_llm_usage(
        model="gpt-4o-mini",
        prompt_tokens=10_000,
        completion_tokens=1_000,
        tenant_ctx=ctx,
        goal_id="g1",
    )
    assert redis.incrbyfloat.call_count == 2


@pytest.mark.asyncio
async def test_record_llm_usage_tenant_from_ctx_object():
    redis = _make_redis()
    tracker = CostTracker(redis=redis)

    ctx = MagicMock()
    ctx.tenant_id = "t-from-obj"
    cost = await tracker.record_llm_usage(
        model="gpt-4o",
        prompt_tokens=500,
        completion_tokens=50,
        tenant_ctx=ctx,
        goal_id="gX",
    )
    assert cost > 0


@pytest.mark.asyncio
async def test_record_llm_usage_tenant_from_string():
    redis = _make_redis()
    tracker = CostTracker(redis=redis)

    ctx_str = "t-string-ctx"
    cost = await tracker.record_llm_usage(
        model="gpt-4o",
        prompt_tokens=500,
        completion_tokens=50,
        tenant_ctx=ctx_str,  # type: ignore[arg-type]
        goal_id="gX",
    )
    assert cost > 0


@pytest.mark.asyncio
async def test_record_llm_usage_with_db():
    redis = _make_redis()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    def db_factory():
        return mock_session

    tracker = CostTracker(redis=redis, db_factory=db_factory)
    ctx = _ctx("t-db")
    await tracker.record_llm_usage(
        model="gpt-4o-mini",
        prompt_tokens=100,
        completion_tokens=50,
        tenant_ctx=ctx,
        goal_id="g1",
        agent_id="agent1",
        role="planner",
        iteration=1,
    )
    mock_session.execute.assert_called()


@pytest.mark.asyncio
async def test_record_llm_usage_redis_error_does_not_raise():
    redis = AsyncMock()
    redis.incrbyfloat = AsyncMock(side_effect=Exception("Redis down"))
    tracker = CostTracker(redis=redis)
    ctx = _ctx("t1")
    # Should not raise
    cost = await tracker.record_llm_usage(
        model="gpt-4o",
        prompt_tokens=100,
        completion_tokens=10,
        tenant_ctx=ctx,
        goal_id="g1",
    )
    assert cost > 0


# ── 5. _check_ewma_anomaly ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ewma_no_redis_returns_none():
    tracker = CostTracker(redis=None)
    result = await tracker._check_ewma_anomaly("t1", None, 5.0)
    assert result is None


@pytest.mark.asyncio
async def test_ewma_first_call_initialises_state():
    redis = _make_redis()
    tracker = CostTracker(redis=redis)
    result = await tracker._check_ewma_anomaly("t1", "agent1", 0.5)
    assert result is None
    redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_ewma_spike_detected():
    baseline = json.dumps({"mean": 0.10, "var": 0.0001})
    redis = _make_redis({"cost_ewma:t1:agent1": baseline})
    tracker = CostTracker(redis=redis)
    anomaly = await tracker._check_ewma_anomaly("t1", "agent1", 2.0)
    assert anomaly is not None
    assert anomaly.anomaly_type == "spike"
    assert anomaly.sigma_deviation > 3.0


@pytest.mark.asyncio
async def test_ewma_no_spike_within_normal_range():
    baseline = json.dumps({"mean": 1.0, "var": 0.25})
    redis = _make_redis({"cost_ewma:t1:agent2": baseline})
    tracker = CostTracker(redis=redis)
    anomaly = await tracker._check_ewma_anomaly("t1", "agent2", 1.1)
    assert anomaly is None


@pytest.mark.asyncio
async def test_ewma_micro_cost_below_threshold():
    baseline = json.dumps({"mean": 0.001, "var": 0.0})
    redis = _make_redis({"cost_ewma:t1:agent3": baseline})
    tracker = CostTracker(redis=redis)
    # cost_usd <= 0.01 → no anomaly even if sigma > threshold
    anomaly = await tracker._check_ewma_anomaly("t1", "agent3", 0.005)
    assert anomaly is None


@pytest.mark.asyncio
async def test_ewma_anomaly_published_to_redis():
    baseline = json.dumps({"mean": 0.10, "var": 0.0001})
    redis = _make_redis({"cost_ewma:t1:agent1": baseline})
    tracker = CostTracker(redis=redis)

    # Call record_llm_usage which triggers publish on anomaly
    ctx = _ctx("t1")
    with patch.object(CostTracker, "_today", return_value="2026-06-29"):
        await tracker.record_llm_usage(
            model="gpt-4o",
            prompt_tokens=100_000,
            completion_tokens=50_000,
            tenant_ctx=ctx,
            goal_id="g-spike",
            agent_id="agent1",
        )
    # publish should have been called for the anomaly channel
    redis.publish.assert_called()


# ── 6. detect_anomaly ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detect_anomaly_no_redis_returns_empty():
    tracker = CostTracker(redis=None)
    result = await tracker.detect_anomaly("t1")
    assert result == []


@pytest.mark.asyncio
async def test_detect_anomaly_sustained_high():
    # mean > 1.0 and std/mean > 0.5 → sustained_high anomaly
    state = json.dumps({"mean": 2.0, "var": 2.0})  # std=sqrt(2)≈1.41, ratio=0.71 > 0.5
    redis = _make_redis({"cost_ewma:t1:agent99": state})
    tracker = CostTracker(redis=redis)
    anomalies = await tracker.detect_anomaly("t1")
    assert len(anomalies) == 1
    assert anomalies[0].anomaly_type == "sustained_high"


@pytest.mark.asyncio
async def test_detect_anomaly_no_anomaly_low_cost():
    state = json.dumps({"mean": 0.5, "var": 0.1})
    redis = _make_redis({"cost_ewma:t1:agent10": state})
    tracker = CostTracker(redis=redis)
    anomalies = await tracker.detect_anomaly("t1")
    assert len(anomalies) == 0


# ── 7. predict_cost ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_predict_cost_no_db_heuristic():
    tracker = CostTracker(redis=None)
    result = await tracker.predict_cost(
        tenant_id="t1",
        agent_id=None,
        goal_description="summarize a document",
        max_iterations=5,
    )
    assert "predicted_cost_usd" in result
    assert result["predicted_cost_usd"] >= 0
    assert result["confidence"] == "low"
    assert result["basis"] == "heuristic_estimate"


@pytest.mark.asyncio
async def test_predict_cost_breakdown_sums_correctly():
    tracker = CostTracker(redis=None)
    result = await tracker.predict_cost(
        tenant_id="t1",
        agent_id=None,
        goal_description="analyze ten contracts",
        max_iterations=10,
    )
    bd = result["breakdown"]
    total = bd["planning_usd"] + bd["execution_usd"] + bd["verification_usd"]
    assert abs(total - result["predicted_cost_usd"]) < 1e-6


@pytest.mark.asyncio
async def test_predict_cost_does_not_write_redis():
    redis = _make_redis({"cost:daily:t1:2026-06-29": "5.0"})
    tracker = CostTracker(redis=redis)
    with patch.object(CostTracker, "_today", return_value="2026-06-29"):
        await tracker.predict_cost(
            tenant_id="t1",
            agent_id=None,
            goal_description="some goal",
        )
    redis.incrbyfloat.assert_not_called()


@pytest.mark.asyncio
async def test_predict_cost_with_db_historical():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (0.25, 0.80)  # p50, p95
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    def db_factory():
        return mock_session

    tracker = CostTracker(redis=None, db_factory=db_factory)
    result = await tracker.predict_cost(
        tenant_id="t1",
        agent_id="agent1",
        goal_description="historical goal",
    )
    assert result["confidence"] == "high"
    assert result["basis"] == "agent_historical_average"
    assert result["predicted_cost_usd"] == pytest.approx(0.25)


# ── 8. get_per_agent_summary ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_per_agent_summary_no_db():
    tracker = CostTracker()
    result = await tracker.get_per_agent_summary("t1")
    assert result == []


@pytest.mark.asyncio
async def test_per_agent_summary_with_db():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [
        ("agent-1", 5.0, 100_000, 20_000, 3),
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    def db_factory():
        return mock_session

    tracker = CostTracker(redis=None, db_factory=db_factory)
    rows = await tracker.get_per_agent_summary("t1")
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "agent-1"
    assert rows[0]["total_cost_usd"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_per_agent_summary_db_error_returns_empty():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("DB error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    def db_factory():
        return mock_session

    tracker = CostTracker(redis=None, db_factory=db_factory)
    result = await tracker.get_per_agent_summary("t1")
    assert result == []


# ── 9. _load_budgets ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_budgets_no_db_returns_defaults():
    tracker = CostTracker()
    budgets = await tracker._load_budgets("t1")
    assert budgets.per_goal_usd == 10.0
    assert budgets.per_tenant_daily_usd == 500.0


@pytest.mark.asyncio
async def test_load_budgets_with_db_row():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (20.0, 1000.0)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    def db_factory():
        return mock_session

    tracker = CostTracker(db_factory=db_factory)
    budgets = await tracker._load_budgets("t1")
    assert budgets.per_goal_usd == pytest.approx(20.0)
    assert budgets.per_tenant_daily_usd == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_load_budgets_db_no_row_returns_defaults():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    def db_factory():
        return mock_session

    tracker = CostTracker(db_factory=db_factory)
    budgets = await tracker._load_budgets("t1")
    assert budgets.per_goal_usd == 10.0


# ── 10. BudgetLimits dataclass ────────────────────────────────────────────────

def test_budget_limits_defaults():
    b = BudgetLimits()
    assert b.per_goal_usd == 10.0
    assert b.per_tenant_daily_usd == 500.0


def test_cost_anomaly_has_detected_at():
    a = CostAnomaly(
        tenant_id="t1",
        agent_id=None,
        anomaly_type="spike",
        cost_actual_usd=5.0,
        cost_baseline_usd=1.0,
        sigma_deviation=4.0,
    )
    assert a.detected_at is not None
    assert "T" in a.detected_at  # ISO format
