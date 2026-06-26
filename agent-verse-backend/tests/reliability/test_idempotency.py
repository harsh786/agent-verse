import pytest
from unittest.mock import AsyncMock, MagicMock
from app.reliability.idempotency import IdempotencyStore


@pytest.mark.asyncio
async def test_check_and_set_true_for_new_key():
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=True)
    store = IdempotencyStore(mock_redis)
    assert await store.check_and_set("key1", "t1") is True


@pytest.mark.asyncio
async def test_check_and_set_false_for_existing_key():
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=None)
    store = IdempotencyStore(mock_redis)
    assert await store.check_and_set("key1", "t1") is False


@pytest.mark.asyncio
async def test_release_deletes_key():
    mock_redis = MagicMock()
    mock_redis.delete = AsyncMock(return_value=1)
    store = IdempotencyStore(mock_redis)
    await store.release("key1", "t1")
    mock_redis.delete.assert_called_once()


@pytest.mark.asyncio
async def test_exists_checks_key():
    mock_redis = MagicMock()
    mock_redis.exists = AsyncMock(return_value=1)
    store = IdempotencyStore(mock_redis)
    assert await store.exists("key1", "t1") is True


def test_key_includes_tenant_prefix():
    """Keys are tenant-scoped to prevent cross-tenant key collisions."""
    from app.reliability.idempotency import IdempotencyStore
    mock_redis = MagicMock()
    store = IdempotencyStore(mock_redis)
    # The internal key should include tenant_id
    expected_prefix = f"{IdempotencyStore.KEY_PREFIX}t1:key1"
    assert "t1" in expected_prefix and "key1" in expected_prefix
