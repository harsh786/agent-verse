"""Tests for LongTermMemory DB persistence via extract_from_goal_async."""
import pytest
from unittest.mock import AsyncMock
from app.memory.long_term import LongTermMemoryStore, LongTermMemory
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="ltm-t1", plan=PlanTier.ENTERPRISE, api_key_id="k")


@pytest.mark.asyncio
async def test_extract_from_goal_async_calls_store_async():
    """extract_from_goal_async must call store_async for DB persistence."""
    store = LongTermMemoryStore()
    store.store_async = AsyncMock(return_value=None)

    await store.extract_from_goal_async(
        goal="Find all open GitHub issues",
        result="Found 42 open issues",
        tenant_ctx=T,
        db=None,
        embedder=None,
    )

    store.store_async.assert_called_once()
    call_kwargs = store.store_async.call_args.kwargs
    assert "memory" in call_kwargs
    assert "Find all open GitHub issues" in call_kwargs["memory"].content


@pytest.mark.asyncio
async def test_extract_from_goal_async_adds_to_in_memory():
    """In-memory list must also be updated for same-session recall."""
    store = LongTermMemoryStore()
    store.store_async = AsyncMock(return_value=None)

    initial_count = len(store._memories)
    await store.extract_from_goal_async(
        goal="test goal",
        result="test result",
        tenant_ctx=T,
    )

    assert len(store._memories) == initial_count + 1
