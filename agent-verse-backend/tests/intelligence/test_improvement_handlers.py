"""Tests for real improvement handler functions (D-3 fix).

These tests verify that the ImprovementActionExecutor, when wired with the
real handlers from ``app.intelligence.improvement_handlers``, actually
executes and returns durable results rather than hitting an empty handler
map and raising RuntimeError.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.intelligence.improvement_action_executor import ImprovementActionExecutor
from app.memory.contracts import ImprovementActionRecord


# ---------------------------------------------------------------------------
# Handler import / existence
# ---------------------------------------------------------------------------


def test_improvement_handlers_module_exports_build_function() -> None:
    from app.intelligence.improvement_handlers import build_default_handlers

    handlers = build_default_handlers()
    assert isinstance(handlers, dict)
    assert len(handlers) >= 3


def test_build_default_handlers_keys_match_action_types() -> None:
    from app.intelligence.improvement_handlers import build_default_handlers

    handlers = build_default_handlers()
    # Must cover the three required action types
    assert "update_prompt_variant" in handlers
    assert "adjust_limit" in handlers
    assert "update_rag_strategy" in handlers


# ---------------------------------------------------------------------------
# prompt_update handler (update_prompt_variant)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_update_handler_returns_durable_result() -> None:
    from app.intelligence.improvement_handlers import build_default_handlers

    handlers = build_default_handlers()
    executor = ImprovementActionExecutor(handlers=handlers)
    record = ImprovementActionRecord(
        action_id="prompt-1",
        tenant_id="t1",
        goal_id="g1",
        action_type="update_prompt_variant",
        payload={
            "prompt_key": "planner",
            "variant_id": "var_001",
            "new_prompt": "You are a careful planner.",
        },
        state="pending",
        idempotency_key="prompt-cmd-1",
        attempts=0,
        created_at=datetime.now(UTC),
    )
    result = await executor.execute(record, policy_allowed=True)
    assert result.state == "completed"
    assert result.result is not None
    assert result.result["stored"] is True
    assert result.result["variant_id"] == "var_001"


@pytest.mark.asyncio
async def test_prompt_update_handler_rejects_missing_prompt_key() -> None:
    from app.intelligence.improvement_handlers import build_default_handlers

    handlers = build_default_handlers()
    executor = ImprovementActionExecutor(handlers=handlers)
    record = ImprovementActionRecord(
        action_id="prompt-bad",
        tenant_id="t1",
        goal_id="g1",
        action_type="update_prompt_variant",
        payload={"variant_id": "var_002"},  # no prompt_key or new_prompt
        state="pending",
        idempotency_key="prompt-cmd-bad",
        attempts=0,
        created_at=datetime.now(UTC),
    )
    result = await executor.execute(record, policy_allowed=True)
    # Should fail after max attempts since handler raises on bad payload
    assert result.state == "failed"


# ---------------------------------------------------------------------------
# parameter_tune handler (adjust_limit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parameter_tune_handler_adjusts_value() -> None:
    from app.intelligence.improvement_handlers import build_default_handlers

    handlers = build_default_handlers()
    executor = ImprovementActionExecutor(handlers=handlers)
    record = ImprovementActionRecord(
        action_id="tune-1",
        tenant_id="t1",
        goal_id="g1",
        action_type="adjust_limit",
        payload={
            "parameter": "max_iterations",
            "old_value": 15,
            "new_value": 8,
        },
        state="pending",
        idempotency_key="tune-cmd-1",
        attempts=0,
        created_at=datetime.now(UTC),
    )
    result = await executor.execute(record, policy_allowed=True)
    assert result.state == "completed"
    assert result.result is not None
    assert result.result["tuned"] is True
    assert result.result["parameter"] == "max_iterations"
    assert result.result["new_value"] == 8


@pytest.mark.asyncio
async def test_parameter_tune_rejects_out_of_bounds() -> None:
    from app.intelligence.improvement_handlers import build_default_handlers

    handlers = build_default_handlers()
    executor = ImprovementActionExecutor(handlers=handlers)
    record = ImprovementActionRecord(
        action_id="tune-bad",
        tenant_id="t1",
        goal_id="g1",
        action_type="adjust_limit",
        payload={
            "parameter": "max_iterations",
            "old_value": 15,
            "new_value": 500,  # way out of bounds
        },
        state="pending",
        idempotency_key="tune-cmd-bad",
        attempts=0,
        created_at=datetime.now(UTC),
    )
    result = await executor.execute(record, policy_allowed=True)
    assert result.state == "failed"


# ---------------------------------------------------------------------------
# strategy_switch handler (update_rag_strategy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strategy_switch_handler_records_preference() -> None:
    from app.intelligence.improvement_handlers import build_default_handlers

    handlers = build_default_handlers()
    executor = ImprovementActionExecutor(handlers=handlers)
    record = ImprovementActionRecord(
        action_id="strat-1",
        tenant_id="t1",
        goal_id="g1",
        action_type="update_rag_strategy",
        payload={
            "strategy": "hybrid_rerank",
            "reason": "Low retrieval confidence",
        },
        state="pending",
        idempotency_key="strat-cmd-1",
        attempts=0,
        created_at=datetime.now(UTC),
    )
    result = await executor.execute(record, policy_allowed=True)
    assert result.state == "completed"
    assert result.result is not None
    assert result.result["switched"] is True
    assert result.result["strategy"] == "hybrid_rerank"


@pytest.mark.asyncio
async def test_strategy_switch_rejects_unknown_strategy() -> None:
    from app.intelligence.improvement_handlers import build_default_handlers

    handlers = build_default_handlers()
    executor = ImprovementActionExecutor(handlers=handlers)
    record = ImprovementActionRecord(
        action_id="strat-bad",
        tenant_id="t1",
        goal_id="g1",
        action_type="update_rag_strategy",
        payload={
            "strategy": "definitely_not_a_real_strategy_!!!",
            "reason": "test",
        },
        state="pending",
        idempotency_key="strat-cmd-bad",
        attempts=0,
        created_at=datetime.now(UTC),
    )
    result = await executor.execute(record, policy_allowed=True)
    assert result.state == "failed"


# ---------------------------------------------------------------------------
# Runtime flag controls handler wiring
# ---------------------------------------------------------------------------


def test_runtime_flag_enable_improvement_handlers_exists() -> None:
    from app.core.runtime_flags import RuntimeFlags

    flags = RuntimeFlags()
    assert hasattr(flags, "enable_improvement_handlers")
    assert flags.enable_improvement_handlers is True  # default True
