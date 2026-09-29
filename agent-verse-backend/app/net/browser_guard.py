"""SSRF egress guard for real (Playwright/Chromium) browsers.

Checking only the URL passed to ``page.goto`` is not enough: Chromium then
follows HTTP redirects, link/click navigations, ``window.open`` popups,
``fetch``/XHR, ``<img>``/``<script>`` subresources and WebSockets on its own —
any of which can point at 169.254.169.254, localhost or an RFC-1918 service.

:func:`new_guarded_context` / :func:`install_browser_guard` close that gap for a
browser context:

* every HTTP(S) request the context makes goes through a ``route`` handler that
  validates the URL with :mod:`app.net.ssrf_guard` (honouring the caller's
  ``allowed_domains``) and aborts blocked ones;
* redirects are NOT left to Chromium — Playwright does not route redirect hops
  ("We do not support intercepting redirects", crNetworkManager), so a public
  URL could 302 to the metadata service unseen. The handler fetches the request
  itself without following redirects, validates every ``Location`` hop, follows
  the chain hop-by-hop and fulfils the browser with the final response. The
  browser therefore never sees a 3xx it could follow unchecked. (Trade-off:
  after a redirected navigation the page URL is the requested URL, not the
  final one.)
* that fetch goes through the IP-pinned client
  (:func:`app.net.ssrf_guard.public_async_client`), which validates the address
  at connect time and dials exactly that IP. Playwright's ``route.fetch`` is not
  used: it resolves DNS again after the check (DNS rebinding). Cookies stay in
  the context's jar: hops get the jar's cookies for their URL, and hop
  ``Set-Cookie`` headers are written back with ``add_cookies``.
* non-network schemes other than ``data:``/``blob:``/``about:`` (``file:``,
  ``ftp:``, ``chrome:``, ...) are aborted;
* WebSockets (not covered by ``route``) are validated via ``route_web_socket``;
* service workers are blocked — their requests bypass ``route``.

Everything fails closed: any error while validating or fetching aborts the
request, and a context whose guard cannot be installed is closed and the error
re-raised.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any
from urllib.parse import urljoin, urlparse

from app.observability.logging import get_logger

logger = get_logger(__name__)

# In-page schemes that never leave the browser process.
_LOCAL_SCHEMES = frozenset({"data", "blob", "about"})
_HTTP_SCHEMES = frozenset({"http", "https"})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 10
# Request headers never replayed onto a (possibly cross-origin) redirect hop.
# Cookies are re-attached by the context's own cookie jar for the new origin.
_DROP_ON_HOP = frozenset(
    {"cookie", "host", "content-length", "authorization", "proxy-authorization"}
)

RouteHandler = Callable[[Any], Awaitable[None]]
# Strong refs for fire-and-forget client closes (context "close" events).
_CLOSE_TASKS: set[Any] = set()


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def browser_url_block_reason(
    url: str, *, allowed_domains: list[str] | None = None, context: str = "browser"
) -> str:
    """Empty string when the browser may load ``url``, else why it is blocked."""
    from app.net.ssrf_guard import SSRFError, assert_public_url_async

    scheme = (urlparse(url).scheme or "").lower()
    if scheme in _LOCAL_SCHEMES:
        return ""
    if scheme not in _HTTP_SCHEMES:
        return f"blocked by SSRF guard: scheme '{scheme}' not allowed in the browser"
    try:
        await assert_public_url_async(url, context=context, allowed_domains=allowed_domains)
    except (SSRFError, ValueError) as exc:
        return f"blocked by SSRF guard: {exc}"
    except Exception as exc:  # fail closed on anything unexpected
        return f"blocked by SSRF guard: validation error: {exc}"
    return ""


# Request headers never sent upstream by the guard's own client: HTTP/2
# pseudo-headers, hop-by-hop headers, and ``accept-encoding`` (the browser may
# advertise codings the client cannot decode; httpx advertises only its own).
_NEVER_FORWARD = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "upgrade",
        "te",
        "trailer",
        "proxy-connection",
        "accept-encoding",
    }
)
# Response headers not handed back to the browser: the body is fulfilled
# already decoded and re-framed.
_NEVER_FULFIL = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection", "keep-alive"}
)
_FETCH_TIMEOUT_S = 30.0


def _is_redirect(response: Any) -> bool:
    return int(getattr(response, "status_code", 0)) in _REDIRECT_STATUSES and bool(
        response.headers.get("location")
    )


async def _abort(route: Any, url: str, reason: str) -> None:
    logger.warning("browser_request_blocked", url=url[:200], reason=reason[:300])
    try:
        await _maybe_await(route.abort("blockedbyclient"))
    except Exception as exc:  # already handled / page closed
        logger.debug("browser_route_abort_failed", error=str(exc)[:200])


async def _browser_request_headers(request: Any) -> dict[str, str]:
    """Every header the browser attached (``all_headers`` includes the cookie)."""
    getter = getattr(request, "all_headers", None)
    raw: Any = None
    if getter is not None:
        raw = await _maybe_await(getter())
    if not isinstance(raw, dict):
        raw = getattr(request, "headers", None)
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _request_body(request: Any) -> bytes | None:
    raw = getattr(request, "post_data_buffer", None)
    return bytes(raw) if isinstance(raw, bytes | bytearray) else None


def _upstream_headers(headers: dict[str, str], *, drop: frozenset[str]) -> dict[str, str]:
    return {
        k: v
        for k, v in headers.items()
        if not k.startswith(":") and k.lower() not in _NEVER_FORWARD and k.lower() not in drop
    }


async def _jar_cookie_header(jar: Any, url: str) -> str:
    """``Cookie`` header value the browser context would send to ``url``."""
    if jar is None:
        return ""
    cookies = await _maybe_await(jar.cookies([url]))
    return "; ".join(
        f"{c['name']}={c['value']}" for c in cookies or [] if c.get("name") is not None
    )


def _playwright_cookies(response: Any) -> list[dict[str, Any]]:
    """``Set-Cookie`` of an httpx response as Playwright ``add_cookies`` dicts."""
    out: list[dict[str, Any]] = []
    for c in response.cookies.jar:
        cookie: dict[str, Any] = {
            "name": c.name,
            "value": c.value or "",
            "domain": c.domain,
            "path": c.path or "/",
            "secure": bool(c.secure),
            "httpOnly": bool(c.has_nonstandard_attr("HttpOnly")),
        }
        if c.expires is not None:
            cookie["expires"] = float(c.expires)
        out.append(cookie)
    return out


async def _store_cookies(jar: Any, response: Any) -> None:
    if jar is None:
        return
    cookies = _playwright_cookies(response)
    if cookies:
        await _maybe_await(jar.add_cookies(cookies))


def _fulfil_headers(response: Any, *, keep_set_cookie: bool) -> dict[str, str]:
    merged: dict[str, list[str]] = {}
    for key, value in response.headers.multi_items():
        name = key.lower()
        if name in _NEVER_FULFIL or (name == "set-cookie" and not keep_set_cookie):
            continue
        merged.setdefault(name, []).append(value)
    # Playwright splits a multi-valued Set-Cookie on newlines.
    return {k: ("\n" if k == "set-cookie" else ", ").join(v) for k, v in merged.items()}


def make_route_guard(
    *,
    allowed_domains: list[str] | None = None,
    context: str = "browser",
    cookie_jar: Callable[[], Any] | None = None,
    http_client: Callable[[], Any] | None = None,
    max_redirects: int = MAX_REDIRECTS,
) -> RouteHandler:
    """Build a Playwright ``route`` handler enforcing the SSRF policy.

    The request is performed by an IP-pinned ``httpx`` client
    (:func:`app.net.ssrf_guard.public_async_client`, one pooled client per
    guard) — never by Playwright's ``route.fetch``, which resolves DNS itself
    after the check and so reopened a DNS-rebinding window. The pinned client
    re-resolves, validates and dials the checked address at connect time.

    ``cookie_jar`` returns the ``BrowserContext`` (``cookies``/``add_cookies``):
    redirect hops get the jar's cookies for their own URL, and ``Set-Cookie``
    from hops the browser never saw is stored there. Without it hops carry no
    cookies. ``http_client`` injects a client factory (tests); an injected
    client belongs to the caller and is not closed by :func:`aclose`.
    The returned handler has an ``aclose()`` coroutine function.
    """
    owned: dict[str, Any] = {}

    def _client() -> Any:
        if http_client is not None:
            return http_client()
        if "client" not in owned:
            from app.net import ssrf_guard

            owned["client"] = ssrf_guard.public_async_client(
                allowed_domains=allowed_domains, timeout=_FETCH_TIMEOUT_S
            )
        return owned["client"]

    async def _guard(route: Any) -> None:
        request = route.request
        url = str(request.url)
        reason = await browser_url_block_reason(
            url, allowed_domains=allowed_domains, context=context
        )
        if reason:
            await _abort(route, url, reason)
            return
        if (urlparse(url).scheme or "").lower() not in _HTTP_SCHEMES:
            await _maybe_await(route.continue_())  # data:/blob:/about:
            return

        try:
            client = _client()
            jar = cookie_jar() if cookie_jar is not None else None
            browser_headers = await _browser_request_headers(request)
            method = str(getattr(request, "method", "GET") or "GET").upper()
            body = _request_body(request)
            # First hop: exactly what the browser sent to this origin.
            response = await client.request(
                method,
                url,
                headers=_upstream_headers(browser_headers, drop=frozenset()),
                content=body,
            )
            current_url = url
            hops = 0
            while _is_redirect(response):
                hops += 1
                next_url = urljoin(current_url, response.headers.get("location", ""))
                if hops > max_redirects:
                    await _abort(route, next_url, f"too many redirects (>{max_redirects})")
                    return
                reason = await browser_url_block_reason(
                    next_url, allowed_domains=allowed_domains, context=context
                )
                if reason:
                    await _abort(route, next_url, f"redirect from {current_url}: {reason}")
                    return
                # The browser never sees this 3xx, so keep its cookies ourselves.
                await _store_cookies(jar, response)
                status = int(response.status_code)
                if status == 303 or (status in (301, 302) and method not in ("GET", "HEAD")):
                    method, body = ("HEAD" if method == "HEAD" else "GET"), None
                headers = {
                    k: v
                    for k, v in _upstream_headers(browser_headers, drop=_DROP_ON_HOP).items()
                    if not (body is None and k.lower() == "content-type")
                }
                cookie = await _jar_cookie_header(jar, next_url)
                if cookie:
                    headers["cookie"] = cookie
                response = await client.request(method, next_url, headers=headers, content=body)
                current_url = next_url
            # A response from another URL than the browser requested would have
            # its cookies attributed to the wrong origin: store, don't fulfil.
            same_url = current_url == url
            if not same_url:
                await _store_cookies(jar, response)
            status_code = int(response.status_code)
            fulfil_headers = _fulfil_headers(response, keep_set_cookie=same_url)
            content = response.content
        except Exception as exc:
            await _abort(route, url, f"guarded fetch failed: {exc}")
            return

        await _maybe_await(route.fulfill(status=status_code, headers=fulfil_headers, body=content))

    async def aclose() -> None:
        client = owned.pop("client", None)
        if client is not None:
            await client.aclose()

    _guard.aclose = aclose  # type: ignore[attr-defined]
    return _guard


def make_websocket_guard(
    *, allowed_domains: list[str] | None = None, context: str = "browser"
) -> Callable[[Any], Awaitable[None]]:
    """Build a ``route_web_socket`` handler: connect only to public hosts."""

    async def _guard(ws: Any) -> None:
        url = str(getattr(ws, "url", ""))
        scheme, sep, rest = url.partition("://")
        mapped = {"ws": "http", "wss": "https"}.get(scheme.lower())
        reason = (
            await browser_url_block_reason(
                f"{mapped}{sep}{rest}", allowed_domains=allowed_domains, context=context
            )
            if mapped and sep
            else f"blocked by SSRF guard: scheme '{scheme}' not allowed for WebSocket"
        )
        if reason:
            logger.warning("browser_websocket_blocked", url=url[:200], reason=reason[:300])
            try:
                await _maybe_await(ws.close(code=1008, reason="blocked by SSRF guard"))
            except Exception as exc:
                logger.debug("browser_ws_close_failed", error=str(exc)[:200])
            return
        await _maybe_await(ws.connect_to_server())

    return _guard


async def install_browser_guard(
    target: Any,
    *,
    allowed_domains: list[str] | None = None,
    context: str = "browser",
) -> None:
    """Install the request + WebSocket guards on a ``BrowserContext`` (or ``Page``).

    Raises if either cannot be installed — callers must not use ``target`` then.
    """
    # A Page keeps its cookies on its BrowserContext.
    jar = target if hasattr(target, "add_cookies") else getattr(target, "context", None)
    handler = make_route_guard(
        allowed_domains=allowed_domains,
        context=context,
        cookie_jar=lambda: jar,
    )
    await _maybe_await(target.route("**/*", handler))
    # The guard's pinned client lives as long as the context/page.
    on = getattr(target, "on", None)
    if on is not None:
        aclose: Callable[[], Coroutine[Any, Any, None]] = handler.aclose  # type: ignore[attr-defined]

        def _close_client(*_args: Any) -> None:
            import asyncio

            try:
                task = asyncio.get_running_loop().create_task(aclose())
            except RuntimeError:  # no loop: the client is dropped with the process
                return
            _CLOSE_TASKS.add(task)
            task.add_done_callback(_CLOSE_TASKS.discard)

        on("close", _close_client)
    route_ws = getattr(target, "route_web_socket", None)
    if route_ws is None:
        raise RuntimeError(
            "Playwright >= 1.48 is required: route_web_socket is unavailable, so "
            "browser WebSocket egress cannot be SSRF-guarded"
        )
    await _maybe_await(
        route_ws(
            re.compile(r".*"),
            make_websocket_guard(allowed_domains=allowed_domains, context=context),
        )
    )
    # Marker so callers can assert a context/page is guarded before using it.
    try:
        target._agentverse_ssrf_guarded = True
    except Exception:  # pragma: no cover - slotted / frozen objects
        logger.debug("browser_guard_marker_unset")


async def new_guarded_context(
    browser: Any,
    *,
    allowed_domains: list[str] | None = None,
    context: str = "browser",
    **kwargs: Any,
) -> Any:
    """``browser.new_context(**kwargs)`` with the SSRF guard installed.

    Service workers are always blocked: their requests bypass ``route``.
    """
    kwargs["service_workers"] = "block"
    ctx = await browser.new_context(**kwargs)
    try:
        await install_browser_guard(ctx, allowed_domains=allowed_domains, context=context)
    except Exception:
        close = getattr(ctx, "close", None)
        if close is not None:
            try:
                await _maybe_await(close())
            except Exception as exc:
                logger.debug("browser_context_close_failed", error=str(exc)[:200])
        raise
    return ctx
