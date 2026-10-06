"""Test async rollback engine completes actual inverse calls."""
from __future__ import annotations

import asyncio

import pytest

from app.reliability.rollback import RollbackEngine


@pytest.mark.asyncio
async def test_rollback_all_async_awaits_coroutines() -> None:
    """rollback_all_async must await coroutine inverses — not fire-and-forget."""
    engine = RollbackEngine()
    completed: list[str] = []

    async def async_inverse() -> None:
        await asyncio.sleep(0)  # yield to event loop
        completed.append("done")

    engine.register(action="test_action", inverse=async_inverse)
    result = await engine.rollback_all_async()
    assert result == ["test_action"]
    assert completed == ["done"]  # Must be completed, not fire-and-forget


@pytest.mark.asyncio
async def test_rollback_all_async_lifo_order() -> None:
    """Inverses must execute in LIFO order (last registered = first rolled back)."""
    engine = RollbackEngine()
    order: list[str] = []

    def make_inverse(name: str):  # returns an async callable
        async def _inv() -> None:
            order.append(name)

        return _inv

    engine.register(action="first", inverse=make_inverse("first"))
    engine.register(action="second", inverse=make_inverse("second"))
    engine.register(action="third", inverse=make_inverse("third"))
    await engine.rollback_all_async()
    assert order == ["third", "second", "first"]  # LIFO


@pytest.mark.asyncio
async def test_rollback_all_async_continues_on_error() -> None:
    """A failing inverse must not abort subsequent rollbacks."""
    engine = RollbackEngine()
    completed: list[str] = []

    async def failing_inverse() -> None:
        raise ValueError("API call failed")

    async def succeeding_inverse() -> None:
        completed.append("ok")

    # good_action registered first → rolled back last (LIFO means bad_action first)
    engine.register(action="good_action", inverse=succeeding_inverse)
    engine.register(action="bad_action", inverse=failing_inverse)

    result = await engine.rollback_all_async()
    assert "bad_action" not in result   # failed, so not in rolled_back
    assert "good_action" in result
    assert completed == ["ok"]


@pytest.mark.asyncio
async def test_rollback_all_async_with_sync_inverse() -> None:
    """Sync lambdas still work correctly with rollback_all_async."""
    engine = RollbackEngine()
    called: list[str] = []
    engine.register(action="sync_action", inverse=lambda: called.append("sync"))
    result = await engine.rollback_all_async()
    assert result == ["sync_action"]
    assert called == ["sync"]


@pytest.mark.asyncio
async def test_rollback_all_async_empty_stack_returns_empty() -> None:
    """Empty stack returns empty list without error."""
    engine = RollbackEngine()
    result = await engine.rollback_all_async()
    assert result == []


@pytest.mark.asyncio
async def test_rollback_all_sync_refuses_a_coroutine_inverse_in_a_running_loop() -> None:
    """a08-F200-01: inside a running loop the sync rollback_all cannot await an
    async inverse. It used to schedule it fire-and-forget and still report it
    rolled back; now it is not run and is reported as failed."""
    engine = RollbackEngine()
    completed: list[str] = []

    async def async_inv() -> None:
        completed.append("ran")

    engine.register(action="async_action", inverse=async_inv)

    result = engine.rollback_all()
    assert result == []
    assert engine.last_report is not None
    assert [f["action"] for f in engine.last_report.failed] == ["async_action"]
    await asyncio.sleep(0)
    assert completed == []  # nothing was left running in the background


def test_rollback_all_sync_handles_sync_inverse_without_event_loop() -> None:
    """Sync rollback works correctly for sync inverses (no async involvement)."""
    engine = RollbackEngine()
    called: list[str] = []
    engine.register(action="plain_sync", inverse=lambda: called.append("x"))
    result = engine.rollback_all()
    assert result == ["plain_sync"]
    assert called == ["x"]
