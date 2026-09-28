"""Tests for HITLGateway startup restore (maintenance-only, no warm scan)."""
from unittest.mock import AsyncMock

import pytest

from app.governance.hitl import HITLGateway


@pytest.mark.asyncio
async def test_startup_restore_with_no_db_returns_zero():
    gw = HITLGateway()
    count = await gw.startup_restore(db=None)
    assert count == 0


@pytest.mark.asyncio
async def test_startup_restore_only_expires_phantoms():
    """Startup runs the cross-tenant phantom sweep and hydrates nothing.

    The old startup scan pulled every tenant's pending approvals into memory
    through system_session, which fails under the API's NOBYPASSRLS role. Postgres
    is the source of truth; the request paths read it per tenant.
    """
    gw = HITLGateway()
    gw.expire_phantom_approvals = AsyncMock(return_value=5)
    system_db = AsyncMock()
    count = await gw.startup_restore(db=system_db)
    assert count == 5
    gw.expire_phantom_approvals.assert_awaited_once_with(system_db)
    assert gw._requests == {}
    assert not hasattr(gw, "load_pending_from_db_full"), (
        "the cross-tenant pending-approval warm scan must not come back"
    )


def test_hitl_has_startup_restore():
    gw = HITLGateway()
    import asyncio
    assert hasattr(gw, "startup_restore"), "HITLGateway must have startup_restore()"
    assert asyncio.iscoroutinefunction(gw.startup_restore)


def test_migration_0029_exists():
    import os

    from tests._paths import MIGRATIONS_DIR

    files = os.listdir(MIGRATIONS_DIR)
    assert any("0029" in f for f in files), "Migration 0029 (prompt_variants) must exist"
    assert any("prompt" in f and "0029" in f for f in files), \
        "Migration 0029 must be named with 'prompt' or 'variant'"
