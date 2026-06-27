"""Tests for HITLGateway startup restore."""
import pytest
from unittest.mock import AsyncMock
from app.governance.hitl import HITLGateway


@pytest.mark.asyncio
async def test_startup_restore_with_no_db_returns_zero():
    gw = HITLGateway()
    count = await gw.startup_restore(db=None)
    assert count == 0


@pytest.mark.asyncio
async def test_startup_restore_calls_load_pending_full():
    gw = HITLGateway()
    gw.load_pending_from_db_full = AsyncMock(return_value=5)
    count = await gw.startup_restore(db=AsyncMock())
    assert count == 5
    gw.load_pending_from_db_full.assert_called_once()


def test_hitl_has_startup_restore():
    gw = HITLGateway()
    import asyncio
    assert hasattr(gw, "startup_restore"), "HITLGateway must have startup_restore()"
    assert asyncio.iscoroutinefunction(gw.startup_restore)


def test_migration_0029_exists():
    import os
    migrations_dir = "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend/app/db/migrations/versions"
    files = os.listdir(migrations_dir)
    assert any("0029" in f for f in files), "Migration 0029 (prompt_variants) must exist"
    assert any("prompt" in f and "0029" in f for f in files), \
        "Migration 0029 must be named with 'prompt' or 'variant'"
