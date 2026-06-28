"""Comprehensive tests for CostTracker — token extraction, cost calculation,
budget status (read-only), budget enforcement, anomaly detection, prediction,
and per-agent cost breakdown.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.intelligence.cost_tracker import (
    MODEL_PRICING,
    CostAnomaly,
    CostTracker,
    calculate_cost,
)
from app.providers.base import CompletionResponse, TokenUsage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis(data: dict | None = None) -> AsyncMock:
    """Return an AsyncMock that mimics a minimal Redis interface."""
    store: dict[str, str] = dict(data or {})

    redis = AsyncMock()

    async def _get(key: str) -> str | None:
        return store.get(key)

    async def _set(key: str, value: str, **_kw: object) -> None:
        store[key] = value

    async def _setex(key: str, _ttl: int, value: str) -> None:
        store[key] = value

    async def _incrbyfloat(key: str, amount: float) -> float:
        current = float(store.get(key, "0"))
        store[key] = str(current + amount)
        return current + amount

    async def _expire(key: str, _ttl: int) -> bool:
        return key in store

    redis.get = AsyncMock(side_effect=_get)
    redis.set = AsyncMock(side_effect=_set)
    redis.setex = AsyncMock(side_effect=_setex)
    redis.incrbyfloat = AsyncMock(side_effect=_incrbyfloat)
    redis.expire = AsyncMock(side_effect=_expire)
    redis.publish = AsyncMock(return_value=0)

    return redis


def _make_tenant_ctx(tenant_id: str = "tenant-123") -> MagicMock:
    ctx = MagicMock()
    ctx.tenant_id = tenant_id
    return ctx


# ---------------------------------------------------------------------------
# 1. Token extraction from provider responses (via TokenUsage on CompletionResponse)
# ---------------------------------------------------------------------------


def test_token_extraction_from_anthropic_response():
    """AnthropicProvider must populate usage.prompt_tokens and usage.completion_tokens."""
    response = MagicMock()
    response.usage.input_tokens = 1_500
    response.usage.output_tokens = 300
    response.usage.cache_read_input_tokens = 200
    response.model = "claude-sonnet-4-5"
    response.stop_reason = "end_turn"
    response.content = []

    # Simulate what AnthropicProvider does
    usage = TokenUsage(
        prompt_tokens=getattr(response.usage, "input_tokens", 0),
        completion_tokens=getattr(response.usage, "output_tokens", 0),
        total_tokens=(
            getattr(response.usage, "input_tokens", 0)
            + getattr(response.usage, "output_tokens", 0)
        ),
    )

    assert usage.prompt_tokens == 1_500
    assert usage.completion_tokens == 300
    assert usage.total_tokens == 1_800


def test_token_extraction_from_openai_response():
    """OpenAICompatibleProvider must populate usage.prompt_tokens and usage.completion_tokens."""
    response = MagicMock()
    response.usage.prompt_tokens = 2_000
    response.usage.completion_tokens = 500
    response.model = "gpt-4o"
    response.choices = [MagicMock()]
    response.choices[0].message.content = "result"
    response.choices[0].message.tool_calls = None
    response.choices[0].finish_reason = "stop"

    _prompt_tokens = response.usage.prompt_tokens
    _completion_tokens = response.usage.completion_tokens
    usage = TokenUsage(
        prompt_tokens=_prompt_tokens,
        completion_tokens=_completion_tokens,
        total_tokens=_prompt_tokens + _completion_tokens,
    )

    assert usage.prompt_tokens == 2_000
    assert usage.completion_tokens == 500
    assert usage.total_tokens == 2_500


def test_completion_response_usage_field_present():
    """CompletionResponse dataclass must expose a usage field."""
    tu = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    resp = CompletionResponse(content="hello", model="gpt-4o-mini", usage=tu)

    assert resp.usage is not None
    assert resp.usage.prompt_tokens == 100
    assert resp.usage.completion_tokens == 50


def test_completion_response_usage_defaults_none():
    """CompletionResponse.usage must default to None for backwards compat."""
    resp = CompletionResponse(content="hello", model="gpt-4o-mini")
    assert resp.usage is None


# ---------------------------------------------------------------------------
# 2. Cost calculation accuracy
# ---------------------------------------------------------------------------


def test_cost_calculation_accuracy_known_model():
    """100k prompt + 20k completion on claude-sonnet-4-5 = $0.60."""
    # 100_000 * 3.0 / 1_000_000 = 0.30
    # 20_000 * 15.0 / 1_000_000 = 0.30
    # total                       = 0.60
    cost = calculate_cost("claude-sonnet-4-5", 100_000, 20_000)
    assert abs(cost - 0.60) < 1e-6, f"Expected 0.60, got {cost}"


def test_cost_calculation_accuracy_gpt4o_mini():
    """1M prompt + 100k completion on gpt-4o-mini."""
    # 1_000_000 * 0.15 / 1_000_000 = 0.150
    # 100_000  * 0.60  / 1_000_000 = 0.060
    # total                         = 0.210
    cost = calculate_cost("gpt-4o-mini", 1_000_000, 100_000)
    assert abs(cost - 0.210) < 1e-6, f"Expected 0.210, got {cost}"


def test_cost_calculation_unknown_model_uses_fallback():
    """Unknown model should use fallback pricing (3.0/15.0 per 1M tokens)."""
    cost = calculate_cost("unknown-model-xyz", 1_000, 100)
    expected = (1_000 * 3.0 + 100 * 15.0) / 1_000_000
    assert abs(cost - expected) < 1e-9


def test_cost_calculation_zero_tokens_returns_zero():
    cost = calculate_cost("gpt-4o", 0, 0)
    assert cost == 0.0


def test_cost_calculation_all_models_positive():
    for model_id in MODEL_PRICING:
        cost = calculate_cost(model_id, 1_000, 500)
        assert cost > 0, f"Expected positive cost for {model_id}"


# ---------------------------------------------------------------------------
# 3. Budget status is read-only (Amendment 6.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_status_read_only():
    """get_budget_status() must NOT call Redis INCRBYFLOAT."""
    redis = _make_redis({"cost:daily:tenant-abc:2026-06-28": "42.5"})
    tracker = CostTracker(redis=redis)

    # Patch _today so the key matches
    with patch.object(CostTracker, "_today", return_value="2026-06-28"):
        status = await tracker.get_budget_status("tenant-abc")

    assert status["daily_spent"] == 42.5
    redis.incrbyfloat.assert_not_called(), "INCRBYFLOAT must NOT be called in get_budget_status"


@pytest.mark.asyncio
async def test_budget_status_returns_correct_remaining():
    redis = _make_redis({"cost:daily:t1:2026-06-28": "300.0"})
    tracker = CostTracker(redis=redis)

    with patch.object(CostTracker, "_today", return_value="2026-06-28"):
        status = await tracker.get_budget_status("t1")

    assert status["daily_spent"] == 300.0
    assert status["daily_limit"] == 500.0   # default BudgetLimits
    assert status["daily_remaining"] == 200.0
    assert abs(status["budget_pct_remaining"] - 0.40) < 1e-6


@pytest.mark.asyncio
async def test_budget_status_with_no_redis_returns_defaults():
    tracker = CostTracker(redis=None)
    status = await tracker.get_budget_status("tenant-no-redis")

    assert status["daily_spent"] == 0.0
    assert status["daily_limit"] == 500.0
    assert status["daily_remaining"] == 500.0


# ---------------------------------------------------------------------------
# 4. Budget enforcement — record_llm_usage increments counters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_llm_usage_increments_redis_counters():
    """record_llm_usage() must call INCRBYFLOAT for daily and goal keys."""
    redis = _make_redis()
    tracker = CostTracker(redis=redis)
    ctx = _make_tenant_ctx("t-budget")

    cost = await tracker.record_llm_usage(
        model="gpt-4o-mini",
        prompt_tokens=10_000,
        completion_tokens=1_000,
        tenant_ctx=ctx,
        goal_id="goal-1",
    )

    assert cost > 0
    assert redis.incrbyfloat.call_count >= 2  # daily + goal keys


@pytest.mark.asyncio
async def test_record_llm_usage_returns_correct_cost():
    redis = _make_redis()
    tracker = CostTracker(redis=redis)
    ctx = _make_tenant_ctx("t-exact")

    # gpt-4o-mini: 10k prompt * 0.15/1M + 1k completion * 0.60/1M
    expected = (10_000 * 0.15 + 1_000 * 0.60) / 1_000_000
    cost = await tracker.record_llm_usage(
        model="gpt-4o-mini",
        prompt_tokens=10_000,
        completion_tokens=1_000,
        tenant_ctx=ctx,
        goal_id="goal-exact",
    )

    assert abs(cost - expected) < 1e-9


# ---------------------------------------------------------------------------
# 5. Anomaly detection — EWMA z-score
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anomaly_detection_no_anomaly_on_first_call():
    """First observation initialises EWMA; no anomaly returned."""
    redis = _make_redis()
    tracker = CostTracker(redis=redis)

    anomaly = await tracker._check_ewma_anomaly("t1", "agent-1", 1.0)
    assert anomaly is None


@pytest.mark.asyncio
async def test_anomaly_detection_spike_detected():
    """A 10x cost spike on a low-variance baseline must return CostAnomaly."""
    baseline_state = json.dumps({"mean": 0.10, "var": 0.0001})
    redis = _make_redis({"cost_ewma:t1:agent-1": baseline_state})
    tracker = CostTracker(redis=redis)

    anomaly = await tracker._check_ewma_anomaly("t1", "agent-1", 1.0)

    assert anomaly is not None
    assert anomaly.anomaly_type == "spike"
    assert anomaly.sigma_deviation > 3.0
    assert anomaly.cost_actual_usd == 1.0
    assert anomaly.cost_baseline_usd == pytest.approx(0.10, abs=0.01)


@pytest.mark.asyncio
async def test_anomaly_detection_normal_variance_no_anomaly():
    """Cost within ~1 sigma of baseline must NOT trigger an anomaly."""
    # mean=1.0, var=0.25 (std=0.5) → cost=1.2 is within 1 sigma
    baseline_state = json.dumps({"mean": 1.0, "var": 0.25})
    redis = _make_redis({"cost_ewma:t1:agent-2": baseline_state})
    tracker = CostTracker(redis=redis)

    anomaly = await tracker._check_ewma_anomaly("t1", "agent-2", 1.2)
    assert anomaly is None


@pytest.mark.asyncio
async def test_anomaly_detection_tiny_amount_ignored():
    """Micro-costs (<$0.01) must not trigger anomaly even at high sigma."""
    baseline_state = json.dumps({"mean": 0.001, "var": 0.0})
    redis = _make_redis({"cost_ewma:t1:agent-3": baseline_state})
    tracker = CostTracker(redis=redis)

    # 0.005 is technically a spike but below the $0.01 threshold
    anomaly = await tracker._check_ewma_anomaly("t1", "agent-3", 0.005)
    assert anomaly is None


# ---------------------------------------------------------------------------
# 6. predict_cost endpoint — no side-effects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_predict_no_side_effects():
    """predict_cost() must not call Redis INCRBYFLOAT (read-only)."""
    redis = _make_redis({"cost:daily:t-pred:2026-06-28": "10.0"})
    tracker = CostTracker(redis=redis)

    with patch.object(CostTracker, "_today", return_value="2026-06-28"):
        result = await tracker.predict_cost(
            tenant_id="t-pred",
            agent_id=None,
            goal_description="Summarise this document",
            max_iterations=5,
        )

    redis.incrbyfloat.assert_not_called(), "predict_cost must NOT write to Redis"
    assert "predicted_cost_usd" in result
    assert result["predicted_cost_usd"] >= 0
    assert "budget_remaining_usd" in result
    assert result["budget_remaining_usd"] == pytest.approx(490.0, abs=0.1)


@pytest.mark.asyncio
async def test_cost_predict_returns_breakdown():
    redis = _make_redis()
    tracker = CostTracker(redis=redis)

    result = await tracker.predict_cost(
        tenant_id="t-pred2",
        agent_id=None,
        goal_description="Analyse 50 contracts",
        max_iterations=10,
    )

    bd = result["breakdown"]
    assert "planning_usd" in bd
    assert "execution_usd" in bd
    assert "verification_usd" in bd
    # planning + execution + verification ≈ predicted_cost
    total = bd["planning_usd"] + bd["execution_usd"] + bd["verification_usd"]
    assert abs(total - result["predicted_cost_usd"]) < 1e-6


# ---------------------------------------------------------------------------
# 7. Per-agent cost breakdown — database path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_agent_cost_breakdown_no_db_returns_empty():
    tracker = CostTracker(redis=None, db_factory=None)
    result = await tracker.get_per_agent_summary("tenant-no-db")
    assert result == []


@pytest.mark.asyncio
async def test_per_agent_cost_breakdown_with_db():
    """get_per_agent_summary() should execute the aggregation query and return rows."""
    mock_db_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [
        ("agent-uuid-1", 12.50, 500_000, 100_000, 5),
        ("agent-uuid-2",  3.25, 150_000,  30_000, 2),
    ]
    mock_db_session.execute = AsyncMock(return_value=mock_result)
    mock_db_session.__aenter__ = AsyncMock(return_value=mock_db_session)
    mock_db_session.__aexit__ = AsyncMock(return_value=False)

    def _db_factory():
        return mock_db_session

    tracker = CostTracker(redis=None, db_factory=_db_factory)
    rows = await tracker.get_per_agent_summary("tenant-db", days=30)

    assert len(rows) == 2
    assert rows[0]["agent_id"] == "agent-uuid-1"
    assert rows[0]["total_cost_usd"] == pytest.approx(12.50)
    assert rows[0]["goal_count"] == 5
    assert rows[1]["total_cost_usd"] == pytest.approx(3.25)
