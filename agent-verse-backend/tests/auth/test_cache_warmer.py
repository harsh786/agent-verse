"""Comprehensive tests for app/auth/cache_warmer.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.cache_warmer import warm_permission_cache


# ---------------------------------------------------------------------------
# Early-return cases
# ---------------------------------------------------------------------------


async def test_warm_cache_noop_when_redis_is_none():
    """Should return early without any DB queries when redis is None."""
    db_factory = AsyncMock()
    await warm_permission_cache(redis=None, db_factory=db_factory)
    db_factory.assert_not_called()


async def test_warm_cache_noop_when_db_factory_is_none():
    """Should return early without any Redis ops when db_factory is None."""
    redis_mock = AsyncMock()
    await warm_permission_cache(redis=redis_mock, db_factory=None)


async def test_warm_cache_noop_when_both_none():
    await warm_permission_cache(redis=None, db_factory=None)


# ---------------------------------------------------------------------------
# No active tenants
# ---------------------------------------------------------------------------


async def test_warm_cache_no_active_tenants_logs_and_returns():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    result_mock = MagicMock()
    result_mock.fetchall.return_value = []  # No active tenants
    session_mock.execute = AsyncMock(return_value=result_mock)
    db_factory = MagicMock(return_value=session_mock)

    redis_mock = AsyncMock()

    await warm_permission_cache(redis=redis_mock, db_factory=db_factory)
    # Redis should not have been used for warming (no tenants)
    redis_mock.set.assert_not_called()
    redis_mock.setex.assert_not_called()


# ---------------------------------------------------------------------------
# Successful warming
# ---------------------------------------------------------------------------


async def test_warm_cache_warms_active_tenants():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    # First execute call returns active tenant/key rows
    result_mock = MagicMock()
    result_mock.fetchall.return_value = [
        ("tenant-1", "key-1"),
        ("tenant-2", "key-2"),
    ]
    session_mock.execute = AsyncMock(return_value=result_mock)
    # Use MagicMock (not AsyncMock) so db_factory() returns session_mock directly
    db_factory = MagicMock(return_value=session_mock)

    redis_mock = AsyncMock()
    redis_mock.setex = AsyncMock()

    mock_scopes = {"goals:read", "agents:read"}

    with (
        patch(
            "app.auth.scope_enforcement.ScopeEnforcementMiddleware._load_scopes",
            new=AsyncMock(return_value=mock_scopes),
        ),
        patch.object(
            __import__("app.auth.permission_cache", fromlist=["PermissionCache"]).PermissionCache,
            "set",
            new=AsyncMock(),
        ) as mock_perm_set,
    ):
        await warm_permission_cache(redis=redis_mock, db_factory=db_factory)

    # Should have warmed two entries
    assert mock_perm_set.call_count == 2


async def test_warm_cache_skips_failed_keys():
    """Cache warmer should continue when loading scopes for one key fails."""
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    result_mock = MagicMock()
    result_mock.fetchall.return_value = [
        ("tenant-1", "key-good"),
        ("tenant-1", "key-bad"),
    ]
    session_mock.execute = AsyncMock(return_value=result_mock)
    db_factory = MagicMock(return_value=session_mock)

    redis_mock = AsyncMock()

    call_count = 0

    async def _load_scopes_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if kwargs.get("key_id") == "key-bad":
            raise Exception("DB error for this key")
        return {"goals:read"}

    with (
        patch(
            "app.auth.scope_enforcement.ScopeEnforcementMiddleware._load_scopes",
            new=AsyncMock(side_effect=_load_scopes_side_effect),
        ),
        patch.object(
            __import__("app.auth.permission_cache", fromlist=["PermissionCache"]).PermissionCache,
            "set",
            new=AsyncMock(),
        ) as mock_perm_set,
    ):
        await warm_permission_cache(redis=redis_mock, db_factory=db_factory)

    # Only the good key should have been warmed
    assert mock_perm_set.call_count == 1


# ---------------------------------------------------------------------------
# Top-level exception handling
# ---------------------------------------------------------------------------


async def test_warm_cache_handles_db_factory_exception():
    """If the DB factory itself fails, the error is logged but not raised."""
    db_factory = MagicMock(side_effect=Exception("cannot connect to DB"))
    redis_mock = AsyncMock()

    # Should not raise
    await warm_permission_cache(redis=redis_mock, db_factory=db_factory)


async def test_warm_cache_handles_execute_exception():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock(side_effect=Exception("table not found"))
    db_factory = MagicMock(return_value=session_mock)

    redis_mock = AsyncMock()

    # Should not raise — startup errors are non-fatal
    await warm_permission_cache(redis=redis_mock, db_factory=db_factory)


async def test_warm_cache_permission_cache_set_called_with_correct_args():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    result_mock = MagicMock()
    result_mock.fetchall.return_value = [("t1", "k1")]
    session_mock.execute = AsyncMock(return_value=result_mock)
    db_factory = MagicMock(return_value=session_mock)

    redis_mock = AsyncMock()
    mock_scopes = {"goals:read"}

    captured_calls = []

    async def mock_perm_set(self, tenant_id, key_id, scopes):
        captured_calls.append((tenant_id, key_id, scopes))

    from app.auth.permission_cache import PermissionCache

    with (
        patch(
            "app.auth.scope_enforcement.ScopeEnforcementMiddleware._load_scopes",
            new=AsyncMock(return_value=mock_scopes),
        ),
        patch.object(PermissionCache, "set", new=mock_perm_set),
    ):
        await warm_permission_cache(redis=redis_mock, db_factory=db_factory)

    assert len(captured_calls) == 1
    assert captured_calls[0][0] == "t1"
    assert captured_calls[0][1] == "k1"
    assert captured_calls[0][2] == mock_scopes
