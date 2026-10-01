"""SSRF guard for real browsers (app.net.browser_guard).

Checking only the goto URL let Chromium follow redirects, click navigations and
subresource requests to 169.254.169.254 / localhost / RFC-1918. Playwright does
not route redirect hops, so the guard must follow redirects itself.

The guard used to perform that fetch with Playwright's ``route.fetch``, which
resolves DNS itself after the check — a DNS-rebinding window. It now fetches
through the IP-pinned client (app.net.ssrf_guard.public_async_client) and
fulfils the route with that response.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
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


class _Server:
    """A fake origin behind an httpx.MockTransport; records every request.

    Responses are served in order; the last one repeats.
    """

    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.client = httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle), follow_redirects=False
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            return httpx.Response(200, text="ok")
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]

    def factory(self) -> Any:
        return lambda: self.client


def _redirect(status: int, location: str, **headers: str) -> httpx.Response:
    return httpx.Response(status, headers={"location": location, **headers})


def _route(url: str, *, method: str = "GET") -> MagicMock:
    route = MagicMock()
    route.request.url = url
    route.request.method = method
    route.request.headers = {"accept": "*/*", "user-agent": "x"}
    # all_headers() carries the security headers (cookie) the browser attached.
    route.request.all_headers = AsyncMock(
        return_value={
            "accept": "*/*",
            "cookie": "sid=secret",
            "user-agent": "x",
            "accept-encoding": "gzip, deflate, br, zstd",
            ":authority": "ignored",
        }
    )
    route.request.post_data_buffer = None
    route.abort = AsyncMock()
    route.continue_ = AsyncMock()
    route.fulfill = AsyncMock()
    # Playwright's own fetch resolves DNS itself (unpinned) — it must never run.
    route.fetch = AsyncMock(side_effect=AssertionError("route.fetch is not IP-pinned"))
    return route


def _jar(cookies: list[dict[str, Any]] | None = None) -> MagicMock:
    jar = MagicMock()
    jar.cookies = AsyncMock(return_value=cookies or [])
    jar.add_cookies = AsyncMock()
    return jar


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
    server = _Server()
    route = _route(url)
    await make_route_guard(http_client=server.factory())(route)
    route.abort.assert_awaited_once_with("blockedbyclient")
    assert server.requests == []
    route.continue_.assert_not_called()
    route.fulfill.assert_not_called()


@pytest.mark.parametrize("url", ["data:text/html,hi", "blob:https://x/1", "about:blank"])
async def test_in_page_schemes_pass_through(url: str) -> None:
    route = _route(url)
    await make_route_guard(http_client=_Server().factory())(route)
    route.continue_.assert_awaited_once()
    route.abort.assert_not_called()


async def test_public_request_goes_through_the_pinned_client_and_is_fulfilled() -> None:
    server = _Server(
        httpx.Response(
            200,
            headers=[
                ("content-type", "text/html"),
                ("set-cookie", "a=1; Path=/"),
                ("set-cookie", "b=2; Path=/"),
            ],
            content=b"<html>hi</html>",
        )
    )
    route = _route(PUBLIC)
    await make_route_guard(http_client=server.factory())(route)

    route.fetch.assert_not_called()
    route.abort.assert_not_called()
    [sent] = server.requests
    assert str(sent.url) == PUBLIC
    assert sent.headers["cookie"] == "sid=secret"  # first hop: the browser's own cookies
    # httpx advertises only codings it can decode, never the browser's list.
    assert sent.headers.get("accept-encoding") != "gzip, deflate, br, zstd"
    assert ":authority" not in sent.headers
    kwargs = route.fulfill.await_args.kwargs
    assert kwargs["status"] == 200
    assert kwargs["body"] == b"<html>hi</html>"
    assert kwargs["headers"]["content-type"] == "text/html"
    assert kwargs["headers"]["set-cookie"] == "a=1; Path=/\nb=2; Path=/"
    assert "content-length" not in kwargs["headers"]
    assert "content-encoding" not in kwargs["headers"]


async def test_dns_rebinding_between_check_and_connect_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check sees a public IP; the connect would see loopback.

    ``route.fetch`` let Chromium resolve the name again after the check (DNS
    rebinding window). The guard's default client re-resolves and validates at
    connect time and dials only the checked address, so the flip is refused.
    """
    import app.net.ssrf_guard as sg

    answers = iter([["93.184.215.14"], ["127.0.0.1"]])
    monkeypatch.setattr(sg, "_resolve_host", lambda host: next(answers, ["127.0.0.1"]))
    route = _route("http://rebind.example/")

    guard = make_route_guard()
    try:
        await guard(route)
    finally:
        await guard.aclose()  # type: ignore[attr-defined]

    route.fetch.assert_not_called()
    route.fulfill.assert_not_called()
    route.abort.assert_awaited_once_with("blockedbyclient")


async def test_default_client_is_the_ip_pinned_public_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.net.ssrf_guard as sg

    server = _Server()
    seen: list[dict[str, Any]] = []

    def _public(**kwargs: Any) -> httpx.AsyncClient:
        seen.append(kwargs)
        return server.client

    monkeypatch.setattr(sg, "public_async_client", _public)
    guard = make_route_guard(allowed_domains=["corp.example"])
    await guard(_route(PUBLIC))
    await guard(_route(PUBLIC2))
    assert len(seen) == 1, "one pooled client per guard"
    assert seen[0]["allowed_domains"] == ["corp.example"]
    assert len(server.requests) == 2
    await guard.aclose()  # type: ignore[attr-defined]
    assert server.client.is_closed


async def test_redirect_to_metadata_is_aborted_not_handed_to_browser() -> None:
    server = _Server(_redirect(302, METADATA))
    route = _route(PUBLIC)
    await make_route_guard(http_client=server.factory())(route)
    route.abort.assert_awaited_once_with("blockedbyclient")
    route.fulfill.assert_not_called()
    assert [str(r.url) for r in server.requests] == [PUBLIC]


async def test_protocol_relative_redirect_to_loopback_is_aborted() -> None:
    server = _Server(_redirect(301, "//127.0.0.1/admin"))
    route = _route(PUBLIC)
    await make_route_guard(http_client=server.factory())(route)
    route.abort.assert_awaited_once()
    assert len(server.requests) == 1


async def test_second_hop_redirect_to_internal_is_aborted() -> None:
    server = _Server(_redirect(302, PUBLIC2), _redirect(307, "http://10.1.2.3/"))
    route = _route(PUBLIC)
    await make_route_guard(http_client=server.factory())(route)
    assert len(server.requests) == 2
    route.abort.assert_awaited_once()
    route.fulfill.assert_not_called()


async def test_public_redirect_chain_is_followed_hop_by_hop() -> None:
    server = _Server(
        _redirect(302, "/relative"), _redirect(301, PUBLIC2), httpx.Response(200, text="done")
    )
    jar = _jar([{"name": "hop", "value": "v"}])
    route = _route(PUBLIC)
    await make_route_guard(http_client=server.factory(), cookie_jar=lambda: jar)(route)
    urls = [str(r.url) for r in server.requests]
    assert urls == [PUBLIC, "http://93.184.215.14/relative", PUBLIC2]
    for hop in server.requests[1:]:
        # the original cookie header is never replayed; the jar's cookies for
        # the hop URL are attached instead
        assert hop.headers.get("cookie") == "hop=v"
    assert [c.args[0] for c in jar.cookies.await_args_list] == [
        ["http://93.184.215.14/relative"],
        [PUBLIC2],
    ]
    assert route.fulfill.await_args.kwargs["body"] == b"done"


async def test_redirect_hop_without_cookie_jar_sends_no_cookies() -> None:
    server = _Server(_redirect(302, PUBLIC2), httpx.Response(200))
    route = _route(PUBLIC)
    await make_route_guard(http_client=server.factory())(route)
    assert "cookie" not in server.requests[1].headers
    route.fulfill.assert_awaited_once()


async def test_redirect_set_cookie_lands_in_the_browser_jar() -> None:
    server = _Server(
        _redirect(302, PUBLIC2, **{"set-cookie": "session=abc; Path=/; HttpOnly"}),
        httpx.Response(200, headers={"set-cookie": "late=1; Path=/"}),
    )
    jar = _jar()
    route = _route(PUBLIC)
    await make_route_guard(http_client=server.factory(), cookie_jar=lambda: jar)(route)
    added = [c for call in jar.add_cookies.await_args_list for c in call.args[0]]
    by_name = {c["name"]: c for c in added}
    assert by_name["session"]["value"] == "abc"
    assert by_name["session"]["domain"] == "93.184.215.14"
    assert by_name["session"]["httpOnly"] is True
    # The final response came from another URL than the browser asked for, so
    # its cookies go to the jar, never through fulfil (wrong origin).
    assert by_name["late"]["domain"] == "8.8.8.8"
    assert "set-cookie" not in route.fulfill.await_args.kwargs["headers"]


async def test_post_303_becomes_get_without_body() -> None:
    server = _Server(_redirect(303, PUBLIC2), httpx.Response(200))
    route = _route(PUBLIC, method="POST")
    route.request.post_data_buffer = b"a=1"
    await make_route_guard(http_client=server.factory())(route)
    first, second = server.requests
    assert first.method == "POST" and first.content == b"a=1"
    assert second.method == "GET" and second.content == b""


async def test_too_many_redirects_fails_closed() -> None:
    server = _Server(_redirect(302, PUBLIC2))
    route = _route(PUBLIC)
    await make_route_guard(http_client=server.factory(), max_redirects=3)(route)
    route.abort.assert_awaited_once()
    route.fulfill.assert_not_called()
    assert len(server.requests) == 4


async def test_fetch_error_fails_closed() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_boom))
    route = _route(PUBLIC)
    await make_route_guard(http_client=lambda: client)(route)
    route.abort.assert_awaited_once()
    route.fulfill.assert_not_called()


async def test_allowed_domains_opens_private_host(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.net.ssrf_guard as sg

    monkeypatch.setattr(sg, "_resolve_host", lambda host: ["10.0.0.5"])
    url = "http://staging.corp.example/"
    server = _Server()
    blocked = _route(url)
    await make_route_guard(http_client=server.factory())(blocked)
    blocked.abort.assert_awaited_once()

    allowed = _route(url)
    await make_route_guard(allowed_domains=["corp.example"], http_client=server.factory())(
        allowed
    )
    allowed.abort.assert_not_called()
    allowed.fulfill.assert_awaited_once()


async def test_allowed_domains_never_opens_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.net.ssrf_guard as sg

    monkeypatch.setattr(sg, "_resolve_host", lambda host: ["169.254.169.254"])
    route = _route("http://evil.corp.example/")
    await make_route_guard(allowed_domains=["corp.example"], http_client=_Server().factory())(
        route
    )
    route.abort.assert_awaited_once()


async def test_guard_aclose_leaves_an_injected_client_to_its_owner() -> None:
    injected = _Server()
    guard = make_route_guard(http_client=injected.factory())
    await guard(_route(PUBLIC))
    await guard.aclose()  # type: ignore[attr-defined]
    assert not injected.client.is_closed


class _WsRoute:
    """Minimal Playwright ``WebSocketRoute`` double (page side of the socket)."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.sent: list[Any] = []
        self.closed: list[dict[str, Any]] = []
        self.connect_to_server = MagicMock()
        self._on_message: Any = None
        self._on_close: Any = None

    def on_message(self, handler: Any) -> None:
        self._on_message = handler

    def on_close(self, handler: Any) -> None:
        self._on_close = handler

    def send(self, message: Any) -> None:
        self.sent.append(message)

    async def close(self, **kw: Any) -> None:
        self.closed.append(kw)


class _Upstream:
    """Server-side websockets connection double."""

    def __init__(self, incoming: list[Any]) -> None:
        self._incoming = list(incoming)
        self.sent: list[Any] = []
        self.release = __import__("asyncio").Event()
        self.closed = False

    async def send(self, msg: Any) -> None:
        self.sent.append(msg)

    async def close(self) -> None:
        self.closed = True
        self.release.set()

    def __aiter__(self) -> Any:
        return self._gen()

    async def _gen(self) -> Any:
        for m in self._incoming:
            yield m
        await self.release.wait()


async def _drain() -> None:
    import asyncio

    for _ in range(20):
        await asyncio.sleep(0)


@pytest.mark.parametrize(
    "url",
    ["ws://127.0.0.1:9222/devtools", "wss://169.254.169.254/", "ws://10.0.0.1/", "ftp://x/"],
)
async def test_websocket_guard_blocks_internal_targets(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect = AsyncMock()
    monkeypatch.setattr("websockets.connect", connect)
    ws = _WsRoute(url)
    await make_websocket_guard()(ws)
    assert ws.closed and ws.closed[0]["code"] == 1008
    ws.connect_to_server.assert_not_called()
    connect.assert_not_called()


async def test_websocket_guard_relays_over_a_pinned_connection_never_the_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSRF-03: the browser must not connect_to_server (it re-resolves DNS)."""
    import app.net.ssrf_guard as g

    monkeypatch.setattr(g, "_resolve_host", lambda h: ["93.184.215.14"])
    upstream = _Upstream(["hello from server"])
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _connect(uri: str, **kw: Any) -> Any:
        calls.append((uri, kw))
        return upstream

    monkeypatch.setattr("websockets.connect", _connect)
    jar = MagicMock()
    jar.cookies = AsyncMock(return_value=[{"name": "sid", "value": "abc"}])
    ws = _WsRoute("wss://feed.example/socket")
    await make_websocket_guard(cookie_jar=lambda: jar)(ws)
    ws.connect_to_server.assert_not_called()
    uri, kw = calls[0]
    assert uri == "wss://feed.example/socket"
    assert kw["host"] == "93.184.215.14" and kw["proxy"] is None
    assert kw["additional_headers"] == {"cookie": "sid=abc"}
    await _drain()
    assert ws.sent == ["hello from server"]  # upstream -> page
    ws._on_message("hello from page")
    await _drain()
    assert upstream.sent == ["hello from page"]  # page -> upstream
    ws._on_close(1000, "")
    await _drain()
    assert upstream.closed


async def test_websocket_guard_refuses_a_host_that_rebinds_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.net.ssrf_guard as g

    answers = iter([["93.184.215.14"]])  # first check public, then loopback
    monkeypatch.setattr(g, "_resolve_host", lambda h: next(answers, ["127.0.0.1"]))
    connect = AsyncMock()
    monkeypatch.setattr("websockets.connect", connect)
    ws = _WsRoute("ws://rebind.example/socket")
    await make_websocket_guard()(ws)
    ws.connect_to_server.assert_not_called()
    connect.assert_not_called()
    assert ws.closed and ws.closed[0]["code"] == 1008


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


async def test_install_closes_the_pinned_client_when_the_context_closes() -> None:
    ctx = MagicMock()
    ctx.route = AsyncMock()
    ctx.route_web_socket = AsyncMock()
    await install_browser_guard(ctx)
    assert "close" in [c.args[0] for c in ctx.on.call_args_list]


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
