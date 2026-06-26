"""Tests for BrowserSessionManager and BrowserSession."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rpa.session_manager import BrowserSession, BrowserSessionManager


# ── BrowserSession unit tests ─────────────────────────────────────────────────


def test_browser_session_is_alive_false_when_no_browser() -> None:
    session = BrowserSession(session_id="s1", tenant_id="t1")
    assert session.is_alive is False


def test_browser_session_page_is_none_by_default() -> None:
    session = BrowserSession(session_id="s1", tenant_id="t1")
    assert session.page is None


def test_browser_session_touch_updates_last_used_at() -> None:
    session = BrowserSession(session_id="s1", tenant_id="t1")
    before = session.last_used_at
    time.sleep(0.01)
    session.touch()
    assert session.last_used_at > before


def test_browser_session_is_alive_true_when_browser_set() -> None:
    session = BrowserSession(session_id="s1", tenant_id="t1")
    session._browser = MagicMock()
    assert session.is_alive is True


@pytest.mark.asyncio
async def test_browser_session_close_sets_browser_to_none() -> None:
    session = BrowserSession(session_id="s1", tenant_id="t1")
    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    session._browser = mock_browser
    session._playwright = mock_playwright

    await session.close()

    assert session._browser is None
    assert session._playwright is None
    assert session._context is None
    assert session._page is None
    mock_browser.close.assert_awaited_once()
    mock_playwright.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_browser_session_close_does_not_raise_when_already_closed() -> None:
    session = BrowserSession(session_id="s1", tenant_id="t1")
    # Already no browser — should not raise
    await session.close()
    await session.close()  # idempotent


@pytest.mark.asyncio
async def test_browser_session_close_swallows_browser_errors() -> None:
    session = BrowserSession(session_id="s1", tenant_id="t1")
    mock_browser = AsyncMock()
    mock_browser.close.side_effect = RuntimeError("connection lost")
    mock_playwright = AsyncMock()
    mock_playwright.stop.side_effect = RuntimeError("already stopped")
    session._browser = mock_browser
    session._playwright = mock_playwright

    # Should not raise
    await session.close()
    assert session._browser is None


# ── BrowserSessionManager unit tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_create_returns_browser_session() -> None:
    manager = BrowserSessionManager()
    session = await manager.get_or_create("sid-1", "tenant-1")
    assert isinstance(session, BrowserSession)
    assert session.session_id == "sid-1"
    assert session.tenant_id == "tenant-1"


@pytest.mark.asyncio
async def test_get_or_create_same_id_returns_same_session() -> None:
    manager = BrowserSessionManager()
    # Inject a fake alive session so the second call returns the same object
    fake = BrowserSession(session_id="sid-2", tenant_id="t1")
    fake._browser = MagicMock()  # makes is_alive True
    manager._sessions[("sid-2", "t1")] = fake

    retrieved = await manager.get_or_create("sid-2", "t1")
    assert retrieved is fake


@pytest.mark.asyncio
async def test_get_or_create_different_tenants_get_different_sessions() -> None:
    manager = BrowserSessionManager()
    # Pre-populate two sessions with the same session_id but different tenants
    s1 = BrowserSession(session_id="sid", tenant_id="t1")
    s1._browser = MagicMock()
    s2 = BrowserSession(session_id="sid", tenant_id="t2")
    s2._browser = MagicMock()
    manager._sessions[("sid", "t1")] = s1
    manager._sessions[("sid", "t2")] = s2

    r1 = await manager.get_or_create("sid", "t1")
    r2 = await manager.get_or_create("sid", "t2")
    assert r1 is not r2
    assert r1.tenant_id == "t1"
    assert r2.tenant_id == "t2"


@pytest.mark.asyncio
async def test_close_removes_session_from_registry() -> None:
    manager = BrowserSessionManager()
    fake = BrowserSession(session_id="s-close", tenant_id="t1")
    fake._browser = MagicMock()
    fake._playwright = AsyncMock()
    manager._sessions[("s-close", "t1")] = fake

    removed = await manager.close("s-close", "t1")

    assert removed is True
    assert ("s-close", "t1") not in manager._sessions


@pytest.mark.asyncio
async def test_close_unknown_session_returns_false() -> None:
    manager = BrowserSessionManager()
    result = await manager.close("not-here", "t1")
    assert result is False


@pytest.mark.asyncio
async def test_cleanup_expired_removes_idle_sessions() -> None:
    manager = BrowserSessionManager(max_idle_seconds=1)
    old = BrowserSession(session_id="old", tenant_id="t1")
    old._browser = MagicMock()
    old._playwright = AsyncMock()
    # Force last_used_at to the past
    old.last_used_at = time.monotonic() - 10  # 10 s ago
    manager._sessions[("old", "t1")] = old

    fresh = BrowserSession(session_id="fresh", tenant_id="t1")
    fresh._browser = MagicMock()
    manager._sessions[("fresh", "t1")] = fresh

    count = await manager.cleanup_expired()

    assert count == 1
    assert ("old", "t1") not in manager._sessions
    assert ("fresh", "t1") in manager._sessions


@pytest.mark.asyncio
async def test_cleanup_expired_returns_count_of_removed_sessions() -> None:
    manager = BrowserSessionManager(max_idle_seconds=1)
    for i in range(3):
        s = BrowserSession(session_id=f"s{i}", tenant_id="t1")
        s._browser = MagicMock()
        s._playwright = AsyncMock()
        s.last_used_at = time.monotonic() - 100
        manager._sessions[(f"s{i}", "t1")] = s

    count = await manager.cleanup_expired()
    assert count == 3
    assert len(manager._sessions) == 0


@pytest.mark.asyncio
async def test_list_active_returns_all_sessions() -> None:
    manager = BrowserSessionManager()
    s1 = BrowserSession(session_id="a", tenant_id="t1")
    s1._browser = MagicMock()
    s2 = BrowserSession(session_id="b", tenant_id="t2")
    s2._browser = MagicMock()
    manager._sessions[("a", "t1")] = s1
    manager._sessions[("b", "t2")] = s2

    all_active = manager.list_active()
    assert len(all_active) == 2


@pytest.mark.asyncio
async def test_list_active_filters_by_tenant_id() -> None:
    manager = BrowserSessionManager()
    s1 = BrowserSession(session_id="a", tenant_id="t1")
    s1._browser = MagicMock()
    s2 = BrowserSession(session_id="b", tenant_id="t2")
    s2._browser = MagicMock()
    manager._sessions[("a", "t1")] = s1
    manager._sessions[("b", "t2")] = s2

    t1_only = manager.list_active(tenant_id="t1")
    assert len(t1_only) == 1
    assert t1_only[0]["session_id"] == "a"
    assert t1_only[0]["tenant_id"] == "t1"


@pytest.mark.asyncio
async def test_list_active_includes_expected_fields() -> None:
    manager = BrowserSessionManager()
    s = BrowserSession(session_id="x", tenant_id="tx")
    s._browser = MagicMock()
    s.current_url = "https://example.com"
    manager._sessions[("x", "tx")] = s

    result = manager.list_active()
    assert len(result) == 1
    entry = result[0]
    assert entry["session_id"] == "x"
    assert entry["tenant_id"] == "tx"
    assert entry["current_url"] == "https://example.com"
    assert entry["is_alive"] is True
    assert isinstance(entry["idle_seconds"], int)
