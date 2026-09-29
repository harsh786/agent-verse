"""RPA real-browser SSRF: every RPA browser context must carry the request guard.

Only the first URL used to be checked; Chromium then followed redirects, click
navigations and subresource requests to internal hosts unchecked.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rpa.executor import RPAExecutor
from app.rpa.session_manager import BrowserSessionManager

METADATA = "http://169.254.169.254/latest/meta-data/"


def _pw_stack() -> tuple[MagicMock, MagicMock, MagicMock]:
    page = MagicMock()
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value="t")
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.route = AsyncMock()
    context.route_web_socket = AsyncMock()
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    pw = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser)
    pw.stop = AsyncMock()
    pw.__aenter__ = AsyncMock(return_value=pw)
    pw.__aexit__ = AsyncMock(return_value=None)
    starter = MagicMock()
    starter.start = AsyncMock(return_value=pw)
    starter.__aenter__ = AsyncMock(return_value=pw)
    starter.__aexit__ = AsyncMock(return_value=None)
    api = MagicMock()
    api.async_playwright = MagicMock(return_value=starter)
    return api, browser, context


def _mock_route(url: str) -> MagicMock:
    route = MagicMock()
    route.request.url = url
    route.abort = AsyncMock()
    route.continue_ = AsyncMock()
    route.fulfill = AsyncMock()
    route.fetch = AsyncMock()
    return route


async def _assert_guard_blocks(context: MagicMock, url: str = METADATA) -> None:
    assert context.route.await_args is not None, "no route handler installed"
    pattern, handler = context.route.await_args.args[:2]
    assert pattern == "**/*"
    route = _mock_route(url)
    await handler(route)
    route.abort.assert_awaited_once()
    route.fetch.assert_not_called()


async def test_session_manager_context_installs_ssrf_route_guard() -> None:
    api, browser, context = _pw_stack()
    with patch.dict(sys.modules, {"playwright": MagicMock(), "playwright.async_api": api}):
        session = await BrowserSessionManager()._create_session("s", "t")
    assert browser.new_context.await_args.kwargs["service_workers"] == "block"
    context.route_web_socket.assert_awaited_once()
    assert session.ssrf_guarded is True
    await _assert_guard_blocks(context)


async def test_session_manager_honours_allowed_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.net.ssrf_guard as sg

    monkeypatch.setattr(sg, "_resolve_host", lambda host: ["10.0.0.9"])
    api, _browser, context = _pw_stack()
    with patch.dict(sys.modules, {"playwright": MagicMock(), "playwright.async_api": api}):
        await BrowserSessionManager()._create_session(
            "s", "t", allowed_domains=["intranet.example"]
        )
    handler = context.route.await_args.args[1]
    route = _mock_route("http://wiki.intranet.example/")
    route.fetch = AsyncMock(return_value=MagicMock(status=200, headers={}))
    await handler(route)
    route.abort.assert_not_called()
    route.fulfill.assert_awaited_once()
    await _assert_guard_blocks(context, "http://other.example.internal/")


async def test_session_manager_fails_closed_when_guard_cannot_install() -> None:
    api, browser, context = _pw_stack()
    context.route = AsyncMock(side_effect=RuntimeError("no routing"))
    with (
        patch.dict(sys.modules, {"playwright": MagicMock(), "playwright.async_api": api}),
        pytest.raises(RuntimeError),
    ):
        await BrowserSessionManager()._create_session("s", "t")
    context.new_page.assert_not_called()
    browser.close.assert_awaited()


async def test_executor_passes_allowed_domains_to_session_manager() -> None:
    session = MagicMock()
    session.page = MagicMock(goto=AsyncMock(), title=AsyncMock(return_value="x"))
    session.ssrf_guarded = True
    sm = MagicMock(get_or_create=AsyncMock(return_value=session), close=AsyncMock())
    ex = RPAExecutor(session_manager=sm, allowed_domains=["intranet.example"])
    ex._playwright_available = True
    res = await ex.execute(
        tool_name="rpa_open_url", arguments={"url": "https://93.184.215.14/"}, session_id="s1"
    )
    assert res.success, res.error
    assert sm.get_or_create.await_args.kwargs["allowed_domains"] == ["intranet.example"]


async def test_executor_refuses_an_unguarded_session_page() -> None:
    session = MagicMock()
    session.page = MagicMock(goto=AsyncMock())
    session.ssrf_guarded = False
    sm = MagicMock(get_or_create=AsyncMock(return_value=session), close=AsyncMock())
    ex = RPAExecutor(session_manager=sm)
    ex._playwright_available = True
    res = await ex.execute(
        tool_name="rpa_open_url", arguments={"url": "https://93.184.215.14/"}, session_id="s1"
    )
    assert res.success is False
    assert "SSRF" in (res.error or "")
    session.page.goto.assert_not_called()


async def test_executor_session_creation_failure_fails_closed() -> None:
    sm = MagicMock(
        get_or_create=AsyncMock(side_effect=RuntimeError("guard install failed")),
        close=AsyncMock(),
    )
    ex = RPAExecutor(session_manager=sm)
    ex._playwright_available = True
    res = await ex.execute(
        tool_name="rpa_open_url", arguments={"url": "https://93.184.215.14/"}, session_id="s1"
    )
    assert res.success is False


async def test_standalone_executor_context_installs_ssrf_route_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.net.ssrf_guard as sg

    monkeypatch.setattr(sg, "_resolve_host", lambda host: ["10.0.0.9"])
    api, browser, context = _pw_stack()
    ex = RPAExecutor(allowed_domains=["intranet.example"])
    ex._playwright_available = True
    with patch.dict(sys.modules, {"playwright": MagicMock(), "playwright.async_api": api}):
        res = await ex.execute(
            tool_name="rpa_open_url", arguments={"url": "https://93.184.215.14/"}
        )
    assert res.success, res.error
    assert browser.new_context.await_args.kwargs["service_workers"] == "block"
    await _assert_guard_blocks(context)
    # the executor's allowlist reaches the browser guard
    handler: Any = context.route.await_args.args[1]
    route = _mock_route("http://wiki.intranet.example/")
    route.fetch = AsyncMock(return_value=MagicMock(status=200, headers={}))
    await handler(route)
    route.abort.assert_not_called()
