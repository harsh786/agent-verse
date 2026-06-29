"""Comprehensive tests for app/governance/legal_holds.py — targeting 90%+ coverage."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.governance.legal_holds import LegalHoldManager, _CACHE_KEY, _CACHE_TTL


def _make_legal_hold_manager(redis=None, db=None) -> LegalHoldManager:
    return LegalHoldManager(redis=redis, db_factory=db)


# ── create_hold ───────────────────────────────────────────────────────────────

class TestCreateHold:
    async def test_create_hold_no_db_no_redis(self) -> None:
        mgr = _make_legal_hold_manager()
        result = await mgr.create_hold(
            tenant_id="t1",
            name="Hold A",
            resource_type="goal",
            resource_ids=["r1", "r2"],
        )
        assert result["tenant_id"] == "t1"
        assert result["name"] == "Hold A"
        assert result["status"] == "active"
        assert "id" in result

    async def test_create_hold_warms_redis_cache(self) -> None:
        mock_redis = AsyncMock()
        mgr = _make_legal_hold_manager(redis=mock_redis)
        await mgr.create_hold(
            tenant_id="t1",
            name="Hold A",
            resource_type="goal",
            resource_ids=["r1", "r2"],
        )
        mock_redis.sadd.assert_called_once()
        mock_redis.expire.assert_called_once_with(
            _CACHE_KEY.format(tenant_id="t1"), _CACHE_TTL
        )

    async def test_create_hold_empty_resource_ids_skips_redis(self) -> None:
        mock_redis = AsyncMock()
        mgr = _make_legal_hold_manager(redis=mock_redis)
        await mgr.create_hold(
            tenant_id="t1", name="Hold B", resource_type="goal", resource_ids=[]
        )
        mock_redis.sadd.assert_not_called()

    async def test_create_hold_redis_error_suppressed(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.sadd.side_effect = Exception("Redis error")
        mgr = _make_legal_hold_manager(redis=mock_redis)
        result = await mgr.create_hold(
            tenant_id="t1", name="Hold C", resource_type="goal", resource_ids=["r1"]
        )
        assert result["id"] is not None  # still succeeds

    async def test_create_hold_with_all_optional_fields(self) -> None:
        mgr = _make_legal_hold_manager()
        result = await mgr.create_hold(
            tenant_id="t1",
            name="Full Hold",
            resource_type="user",
            resource_ids=["u1"],
            user_ids=["uid1"],
            date_range_start=datetime(2024, 1, 1, tzinfo=UTC),
            date_range_end=datetime(2024, 12, 31, tzinfo=UTC),
            legal_matter_id="matter-001",
            description="SEC investigation",
            created_by="legal@corp.com",
            expires_at=datetime(2025, 12, 31, tzinfo=UTC),
        )
        assert result["legal_matter_id"] == "matter-001"
        assert "u1" in result["resource_ids"]

    async def test_create_hold_db_error_suppressed(self) -> None:
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin = MagicMock(return_value=mock_session)

        @asynccontextmanager
        async def factory():
            yield mock_session

        mgr = _make_legal_hold_manager(db=factory)
        result = await mgr.create_hold(
            tenant_id="t1", name="Hold D", resource_type="goal"
        )
        assert result["id"] is not None


# ── release_hold ──────────────────────────────────────────────────────────────

class TestReleaseHold:
    async def test_release_hold_no_db_returns_true(self) -> None:
        """When no DB is configured, release_hold skips the DB update and returns True."""
        mgr = _make_legal_hold_manager()
        result = await mgr.release_hold("t1", "hold-id")
        # With no DB, the DB check is skipped and the function returns True
        # (cache sync is a noop, then returns True)
        assert result is True

    async def test_release_hold_db_success(self) -> None:
        mock_result = MagicMock()
        mock_result.rowcount = 1

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.begin = MagicMock()

        # We need async context manager with begin
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_session)
        cm.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        @asynccontextmanager
        async def factory():
            yield mock_session

        # Mock begin as context manager
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_begin)

        mgr = _make_legal_hold_manager(db=factory)

        # sync_cache will also call db — just mock it
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(mgr, "sync_cache", AsyncMock())
            result = await mgr.release_hold("t1", "hold-id")
        assert result is True

    async def test_release_hold_db_not_found(self) -> None:
        mock_result = MagicMock()
        mock_result.rowcount = 0

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_begin)

        @asynccontextmanager
        async def factory():
            yield mock_session

        mgr = _make_legal_hold_manager(db=factory)
        result = await mgr.release_hold("t1", "nonexistent-hold")
        assert result is False

    async def test_release_hold_db_error_returns_false(self) -> None:
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_begin)

        @asynccontextmanager
        async def factory():
            yield mock_session

        mgr = _make_legal_hold_manager(db=factory)
        result = await mgr.release_hold("t1", "hold-id")
        assert result is False


# ── is_under_hold ─────────────────────────────────────────────────────────────

class TestIsUnderHold:
    async def test_not_under_hold_no_redis_no_db(self) -> None:
        mgr = _make_legal_hold_manager()
        result = await mgr.is_under_hold("t1", "resource-1")
        assert result is False

    async def test_under_hold_via_redis_cache_hit(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.smembers = AsyncMock(return_value={"resource-1", "resource-2"})
        mgr = _make_legal_hold_manager(redis=mock_redis)
        result = await mgr.is_under_hold("t1", "resource-1")
        assert result is True

    async def test_not_under_hold_via_redis_cache_hit(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.smembers = AsyncMock(return_value={"other-resource"})
        mgr = _make_legal_hold_manager(redis=mock_redis)
        result = await mgr.is_under_hold("t1", "resource-1")
        assert result is False

    async def test_redis_empty_falls_through_to_db(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.smembers = AsyncMock(return_value=set())

        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=(1,))
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        @asynccontextmanager
        async def factory():
            yield mock_session

        mgr = _make_legal_hold_manager(redis=mock_redis, db=factory)
        result = await mgr.is_under_hold("t1", "resource-1")
        assert result is True

    async def test_redis_bytes_decoded(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.smembers = AsyncMock(return_value={b"resource-1"})
        mgr = _make_legal_hold_manager(redis=mock_redis)
        result = await mgr.is_under_hold("t1", "resource-1")
        assert result is True

    async def test_redis_error_falls_through_to_db(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.smembers = AsyncMock(side_effect=Exception("Redis error"))

        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=None)
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        @asynccontextmanager
        async def factory():
            yield mock_session

        mgr = _make_legal_hold_manager(redis=mock_redis, db=factory)
        result = await mgr.is_under_hold("t1", "resource-1")
        assert result is False

    async def test_db_error_returns_false(self) -> None:
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=Exception("DB error"))

        @asynccontextmanager
        async def factory():
            yield mock_session

        mgr = _make_legal_hold_manager(db=factory)
        result = await mgr.is_under_hold("t1", "resource-1")
        assert result is False


# ── list_holds ────────────────────────────────────────────────────────────────

class TestListHolds:
    async def test_list_holds_no_db_returns_empty(self) -> None:
        mgr = _make_legal_hold_manager()
        result = await mgr.list_holds("t1")
        assert result == []

    async def test_list_holds_returns_rows(self) -> None:
        row = (
            "hold-id", "Hold A", "desc", "goal",
            json.dumps(["r1"]), json.dumps([]),
            None, None, "active", "matter-1",
            "legal@corp.com",
            datetime(2024, 1, 1, tzinfo=UTC),
            None,
            datetime(2025, 1, 1, tzinfo=UTC),
        )

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [row]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        @asynccontextmanager
        async def factory():
            yield mock_session

        mgr = _make_legal_hold_manager(db=factory)
        holds = await mgr.list_holds("t1")
        assert len(holds) == 1
        assert holds[0]["id"] == "hold-id"
        assert holds[0]["name"] == "Hold A"

    async def test_list_holds_error_returns_empty(self) -> None:
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=Exception("DB error"))

        @asynccontextmanager
        async def factory():
            yield mock_session

        mgr = _make_legal_hold_manager(db=factory)
        result = await mgr.list_holds("t1")
        assert result == []


# ── sync_cache ────────────────────────────────────────────────────────────────

class TestSyncCache:
    async def test_sync_cache_no_redis_no_db_noop(self) -> None:
        mgr = _make_legal_hold_manager()
        await mgr.sync_cache("t1")  # no exception

    async def test_sync_cache_rebuilds_from_db(self) -> None:
        rows = [(json.dumps(["r1", "r2"]),), (json.dumps(["r3"]),)]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        @asynccontextmanager
        async def factory():
            yield mock_session

        mock_redis = AsyncMock()
        mgr = _make_legal_hold_manager(redis=mock_redis, db=factory)
        await mgr.sync_cache("t1")

        mock_redis.delete.assert_called_once()
        mock_redis.sadd.assert_called_once()
        # r1, r2, r3 should all be in the sadd call
        sadd_args = mock_redis.sadd.call_args[0]
        assert "r1" in sadd_args
        assert "r2" in sadd_args
        assert "r3" in sadd_args

    async def test_sync_cache_no_active_holds_deletes_key(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        @asynccontextmanager
        async def factory():
            yield mock_session

        mock_redis = AsyncMock()
        mgr = _make_legal_hold_manager(redis=mock_redis, db=factory)
        await mgr.sync_cache("t1")

        mock_redis.delete.assert_called_once()
        mock_redis.sadd.assert_not_called()

    async def test_sync_cache_error_suppressed(self) -> None:
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=Exception("DB error"))

        @asynccontextmanager
        async def factory():
            yield mock_session

        mock_redis = AsyncMock()
        mgr = _make_legal_hold_manager(redis=mock_redis, db=factory)
        await mgr.sync_cache("t1")  # must not raise

    async def test_sync_cache_handles_list_rids(self) -> None:
        """resource_ids already a list (not JSON string) is handled."""
        rows = [(["r1", "r2"],)]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        @asynccontextmanager
        async def factory():
            yield mock_session

        mock_redis = AsyncMock()
        mgr = _make_legal_hold_manager(redis=mock_redis, db=factory)
        await mgr.sync_cache("t1")
        sadd_args = mock_redis.sadd.call_args[0]
        assert "r1" in sadd_args
