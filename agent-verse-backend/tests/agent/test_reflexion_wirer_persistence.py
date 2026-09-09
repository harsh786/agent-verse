# tests/agent/test_reflexion_wirer_persistence.py
"""ReflexionWirer must persist lessons to DB, not just in-memory."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.reflexion_wirer import ReflexionWirer
from app.agent.state import AgentState, GoalStatus
from app.state_runtime.reflexion_store import ReflexionStore
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


async def test_maybe_store_async_writes_to_db(tenant_ctx):
    """maybe_store_async() must call record_async() with DB factory."""
    store = ReflexionStore()
    record_async_calls = []

    async def mock_record_async(**kwargs):
        record_async_calls.append(kwargs)

    store.record_async = mock_record_async
    wirer = ReflexionWirer(store=store, db_factory=MagicMock())

    state = AgentState(goal="delete prod db", tenant_ctx=tenant_ctx, goal_id="g1")
    state.status = GoalStatus.FAILED
    state.verification_feedback = "permission denied"

    result = await wirer.maybe_store_async(state)

    assert result is True
    assert len(record_async_calls) == 1
    assert record_async_calls[0]["tenant_id"] == "t1"
    assert "permission denied" in record_async_calls[0]["lesson"]
    assert record_async_calls[0]["db_factory"] is not None


async def test_maybe_store_async_no_lesson_on_success(tenant_ctx):
    """maybe_store_async() must not store lessons for successful goals."""
    store = ReflexionStore()
    wirer = ReflexionWirer(store=store)
    state = AgentState(goal="list tickets", tenant_ctx=tenant_ctx, goal_id="g1")
    state.status = GoalStatus.COMPLETE
    result = await wirer.maybe_store_async(state)
    assert result is False


def test_maybe_store_sync_still_works(tenant_ctx):
    """maybe_store() (sync) must still work for backward compat."""
    store = ReflexionStore()
    wirer = ReflexionWirer(store=store)
    state = AgentState(goal="delete prod", tenant_ctx=tenant_ctx, goal_id="g1")
    state.status = GoalStatus.FAILED
    state.verification_feedback = "permission denied"
    result = wirer.maybe_store(state)
    assert result is True
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 1


def test_failure_classification():
    """Failure class must be correctly derived from feedback text."""
    from app.agent.reflexion_wirer import _classify_failure
    assert _classify_failure("permission denied") == "auth_failure"
    assert _classify_failure("404 not found") == "context_gap"
    assert _classify_failure("request timeout") == "timeout"
    assert _classify_failure("rate limit exceeded 429") == "rate_limit"
    assert _classify_failure("unknown error") == "unknown"


def test_get_reflexion_wirer_wires_db_factory():
    """get_reflexion_wirer(db_factory=...) must inject DB factory into singleton."""
    import app.agent.reflexion_wirer as _m
    from app.agent.reflexion_wirer import get_reflexion_wirer
    _m._default_reflexion_wirer = None  # reset singleton for test
    mock_db = MagicMock()
    wirer = get_reflexion_wirer(db_factory=mock_db)
    assert wirer._db_factory is mock_db
    _m._default_reflexion_wirer = None  # cleanup
