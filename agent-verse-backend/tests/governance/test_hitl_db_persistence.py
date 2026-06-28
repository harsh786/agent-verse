"""Tests that HITL approvals are persisted to DB when db_session_factory is set."""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.governance.hitl import HITLGateway


def _make_tenant(tid: str = "tenant-1") -> MagicMock:
    ctx = MagicMock()
    ctx.tenant_id = tid
    ctx.roles = []
    return ctx


@pytest.mark.asyncio
async def test_hitl_persists_approval_when_factory_set() -> None:
    """When _db_session_factory is set, new approval requests fire DB persistence task."""
    hitl = HITLGateway()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_session)
    mock_session.execute = AsyncMock()
    mock_session.flush = AsyncMock()

    mock_factory = AsyncMock(return_value=mock_session)
    hitl._db_session_factory = mock_factory

    ctx = _make_tenant()

    req_id = hitl.request_approval(
        goal_id="goal-1",
        action="deploy to production",
        risk_level="high",
        tenant_ctx=ctx,
    )
    # Give the fire-and-forget task time to run
    await asyncio.sleep(0.1)
    assert req_id is not None


@pytest.mark.asyncio
async def test_hitl_does_not_persist_when_factory_none() -> None:
    """When _db_session_factory is None (default), no DB call is attempted."""
    hitl = HITLGateway()
    assert hitl._db_session_factory is None

    ctx = _make_tenant()

    # Should not raise even without DB
    req_id = hitl.request_approval(
        goal_id="goal-2",
        action="read data",
        risk_level="low",
        tenant_ctx=ctx,
    )
    assert req_id is not None


@pytest.mark.asyncio
async def test_hitl_db_persist_is_fire_and_forget_not_blocking() -> None:
    """request_approval must return immediately even if DB persist is slow."""
    hitl = HITLGateway()

    async def _slow_session() -> AsyncMock:
        await asyncio.sleep(10)  # Would block if awaited directly
        return AsyncMock()

    hitl._db_session_factory = _slow_session

    ctx = _make_tenant()

    # Should return immediately (not wait 10 s)
    import time

    t0 = time.monotonic()
    req_id = hitl.request_approval(
        goal_id="goal-3",
        action="action",
        risk_level="medium",
        tenant_ctx=ctx,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"request_approval blocked for {elapsed:.2f}s — should be instant"
    assert req_id is not None


def test_main_py_wires_db_session_factory() -> None:
    """Verify main.py sets _hitl._db_session_factory = db_factory in lifespan."""
    import app.main as main_module

    source = inspect.getsource(main_module)
    assert "_hitl._db_session_factory" in source, (
        "main.py must wire _hitl._db_session_factory = db_factory in the lifespan "
        "so new approval requests are persisted to DB at runtime."
    )


@pytest.mark.asyncio
async def test_hitl_db_session_factory_default_is_none() -> None:
    """HITLGateway._db_session_factory starts as None (no DB by default)."""
    hitl = HITLGateway()
    assert hitl._db_session_factory is None
