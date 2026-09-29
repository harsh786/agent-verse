"""SSRF guard for real browsers (app.net.browser_guard).

Checking only the goto URL let Chromium follow redirects, click navigations and
subresource requests to 169.254.169.254 / localhost / RFC-1918. Playwright does
not route redirect hops, so the guard must follow redirects itself.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.net import browser_guard
from app.net.browser_guard import (
    install_browser_guard,
    make_route_guard,
    make_websocket_guard,
    new_guarded_context,
)

PUBLIC = "http://93.184.215.14/page"  # literal public IP: no DNS in tests
PUBLIC2 = "https://8.8.8.8/next"
METADATA = "http://169.254.169.254/latest/meta-data/"


def _resp(status: int = 200, location: str | None = None) -> MagicMock:
    r = MagicMock()
    r.status = status
    r.headers = {"location": location} if location else {"content-type": "text/html"}
    return r


def _route(url: str, *, fetch_result: Any = None, method: str = "GET") -> MagicMock:
    route = MagicMock()
    route.request.url = url
    route.request.method = method
    route.request.headers = {"accept": "*/*", "cookie": "sid=secret", "user-agent": "x"}
    route.request.post_data_buffer = None
    route.abort = AsyncMock()
    route.continue_ = AsyncMock()
    route.fulfill = AsyncMock()
    route.fetch = AsyncMock(return_value=fetch_result if fetch_result is not None else _resp())
    return route


@pytest.mark.parametrize(
    "url",
    [
        METADATA,
        "http://127.0.0.1:6379/",
        "http://localhost:8000/admin",
        "http://10.0.0.7/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://93.184.215.14/x",
        "chrome://settings",
    ],
)
async def test_blocked_request_is_aborted(url: str) -> None:
    route = _route(url)
    await make_route_guard()(route)
    route.abort.assert_awaited_once_with("blockedbyclient")
    route.fetch.assert_not_called()
    route.continue_.assert_not_called()
    route.fulfill.assert_not_called()


@pytest.mark.parametrize("url", ["data:text/html,hi", "blob:https://x/1", "about:blank"])
async def test_in_page_schemes_pass_through(url: str) -> None:
    route = _route(url)
    await make_route_guard()(route)
    route.continue_.assert_awaited_once()
    route.abort.assert_not_called()


async def test_public_request_is_fetched_without_redirects_and_fulfilled() -> None:
    final = _resp(200)
    route = _route(PUBLIC, fetch_result=final)
    await make_route_guard()(route)
    route.fetch.assert_awaited_once_with(max_redirects=0)
    route.fulfill.assert_awaited_once_with(response=final)
    route.abort.assert_not_called()


async def test_redirect_to_metadata_is_aborted_not_handed_to_browser() -> None:
    route = _route(PUBLIC, fetch_result=_resp(302, METADATA))
    api = MagicMock(fetch=AsyncMock())
    await make_route_guard(request_context=lambda: api)(route)
    route.abort.assert_awaited_once_with("blockedbyclient")
    route.fulfill.assert_not_called()
    api.fetch.assert_not_called()


async def test_protocol_relative_redirect_to_loopback_is_aborted() -> None:
    route = _route(PUBLIC, fetch_result=_resp(301, "//127.0.0.1/admin"))
    api = MagicMock(fetch=AsyncMock())
    await make_route_guard(request_context=lambda: api)(route)
    route.abort.assert_awaited_once()
    api.fetch.assert_not_called()


async def test_second_hop_redirect_to_internal_is_aborted() -> None:
    route = _route(PUBLIC, fetch_result=_resp(302, PUBLIC2))
    api = MagicMock(fetch=AsyncMock(return_value=_resp(307, "http://10.1.2.3/")))
    await make_route_guard(request_context=lambda: api)(route)
    api.fetch.assert_awaited_once()
    route.abort.assert_awaited_once()
    route.fulfill.assert_not_called()


async def test_public_redirect_chain_is_followed_hop_by_hop() -> None:
    final = _resp(200)
    route = _route(PUBLIC, fetch_result=_resp(302, "/relative"))
    api = MagicMock(fetch=AsyncMock(side_effect=[_resp(301, PUBLIC2), final]))
    await make_route_guard(request_context=lambda: api)(route)
    urls = [c.args[0] for c in api.fetch.await_args_list]
    assert urls == ["http://93.184.215.14/relative", PUBLIC2]
    for call in api.fetch.await_args_list:
        assert call.kwargs["max_redirects"] == 0
        # the original cookie header is never replayed to another hop
        assert "cookie" not in {k.lower() for k in call.kwargs["headers"]}
    route.fulfill.assert_awaited_once_with(response=final)


async def test_post_303_becomes_get_without_body() -> None:
    route = _route(PUBLIC, fetch_result=_resp(303, PUBLIC2), method="POST")
    route.request.post_data_buffer = b"a=1"
    api = MagicMock(fetch=AsyncMock(return_value=_resp(200)))
    await make_route_guard(request_context=lambda: api)(route)
    kwargs = api.fetch.await_args.kwargs
    assert kwargs["method"] == "GET"
    assert "data" not in kwargs


async def test_redirect_without_request_context_fails_closed() -> None:
    route = _route(PUBLIC, fetch_result=_resp(302, PUBLIC2))
    await make_route_guard()(route)
    route.abort.assert_awaited_once()
    route.fulfill.assert_not_called()


async def test_too_many_redirects_fails_closed() -> None:
    route = _route(PUBLIC, fetch_result=_resp(302, PUBLIC2))
    api = MagicMock(fetch=AsyncMock(return_value=_resp(302, PUBLIC)))
    await make_route_guard(request_context=lambda: api, max_redirects=3)(route)
    route.abort.assert_awaited_once()
    route.fulfill.assert_not_called()


async def test_fetch_error_fails_closed() -> None:
    route = _route(PUBLIC)
    route.fetch = AsyncMock(side_effect=RuntimeError("boom"))
    await make_route_guard()(route)
    route.abort.assert_awaited_once()
    route.fulfill.assert_not_called()


async def test_allowed_domains_opens_private_host(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.net.ssrf_guard as sg

    monkeypatch.setattr(sg, "_resolve_host", lambda host: ["10.0.0.5"])
    url = "http://staging.corp.example/"
    blocked = _route(url)
    await make_route_guard()(blocked)
    blocked.abort.assert_awaited_once()

    allowed = _route(url)
    await make_route_guard(allowed_domains=["corp.example"])(allowed)
    allowed.abort.assert_not_called()
    allowed.fulfill.assert_awaited_once()


async def test_allowed_domains_never_opens_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.net.ssrf_guard as sg

    monkeypatch.setattr(sg, "_resolve_host", lambda host: ["169.254.169.254"])
    route = _route("http://evil.corp.example/")
    await make_route_guard(allowed_domains=["corp.example"])(route)
    route.abort.assert_awaited_once()


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("ws://127.0.0.1:9222/devtools", False),
        ("wss://169.254.169.254/", False),
        ("ws://10.0.0.1/", False),
        ("wss://93.184.215.14/socket", True),
    ],
)
async def test_websocket_guard(url: str, allowed: bool) -> None:
    ws = MagicMock()
    ws.url = url
    ws.close = AsyncMock()
    ws.connect_to_server = MagicMock()
    await make_websocket_guard()(ws)
    if allowed:
        ws.connect_to_server.assert_called_once()
        ws.close.assert_not_called()
    else:
        ws.close.assert_awaited_once()
        ws.connect_to_server.assert_not_called()


async def test_new_guarded_context_installs_guards_and_blocks_service_workers() -> None:
    ctx = MagicMock()
    ctx.route = AsyncMock()
    ctx.route_web_socket = AsyncMock()
    browser = MagicMock(new_context=AsyncMock(return_value=ctx))
    out = await new_guarded_context(browser, viewport={"width": 1, "height": 1})
    assert out is ctx
    assert browser.new_context.await_args.kwargs["service_workers"] == "block"
    assert ctx.route.await_args.args[0] == "**/*"
    ctx.route_web_socket.assert_awaited_once()
    # the installed handler really is the SSRF guard
    handler = ctx.route.await_args.args[1]
    route = _route(METADATA)
    await handler(route)
    route.abort.assert_awaited_once()


async def test_new_guarded_context_closes_and_raises_when_guard_install_fails() -> None:
    ctx = MagicMock()
    ctx.route = AsyncMock(side_effect=RuntimeError("route unsupported"))
    ctx.close = AsyncMock()
    browser = MagicMock(new_context=AsyncMock(return_value=ctx))
    with pytest.raises(RuntimeError):
        await new_guarded_context(browser)
    ctx.close.assert_awaited_once()


async def test_install_requires_websocket_routing() -> None:
    class _OldContext:
        def __init__(self) -> None:
            self.route = AsyncMock()

    with pytest.raises(RuntimeError, match="route_web_socket"):
        await install_browser_guard(_OldContext())


def test_module_exports_max_redirects() -> None:
    assert browser_guard.MAX_REDIRECTS >= 1
