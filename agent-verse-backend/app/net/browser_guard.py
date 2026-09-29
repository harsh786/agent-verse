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
  itself with ``max_redirects=0``, validates every ``Location`` hop, follows the
  chain hop-by-hop and fulfils the browser with the final response. The browser
  therefore never sees a 3xx it could follow unchecked. (Trade-off: after a
  redirected navigation the page URL is the requested URL, not the final one.)
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
from collections.abc import Awaitable, Callable
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


def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None) or {}
    for key, value in dict(headers).items():
        if str(key).lower() == name:
            return str(value)
    return ""


def _is_redirect(response: Any) -> bool:
    return getattr(response, "status", 0) in _REDIRECT_STATUSES and bool(
        _header(response, "location")
    )


async def _abort(route: Any, url: str, reason: str) -> None:
    logger.warning("browser_request_blocked", url=url[:200], reason=reason[:300])
    try:
        await _maybe_await(route.abort("blockedbyclient"))
    except Exception as exc:  # already handled / page closed
        logger.debug("browser_route_abort_failed", error=str(exc)[:200])


def make_route_guard(
    *,
    allowed_domains: list[str] | None = None,
    context: str = "browser",
    request_context: Callable[[], Any] | None = None,
    max_redirects: int = MAX_REDIRECTS,
) -> RouteHandler:
    """Build a Playwright ``route`` handler enforcing the SSRF policy.

    ``request_context`` returns the ``APIRequestContext`` used to follow
    redirect hops (the browser context's ``.request``, which shares its cookie
    jar). Without one, a redirect is refused (fail closed).
    """

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
            response = await _maybe_await(route.fetch(max_redirects=0))
            current_url = url
            method = str(getattr(request, "method", "GET") or "GET").upper()
            body = getattr(request, "post_data_buffer", None)
            hops = 0
            while _is_redirect(response):
                hops += 1
                next_url = urljoin(current_url, _header(response, "location"))
                if hops > max_redirects:
                    await _abort(route, next_url, f"too many redirects (>{max_redirects})")
                    return
                reason = await browser_url_block_reason(
                    next_url, allowed_domains=allowed_domains, context=context
                )
                if reason:
                    await _abort(route, next_url, f"redirect from {current_url}: {reason}")
                    return
                api = request_context() if request_context is not None else None
                if api is None:
                    await _abort(route, next_url, "redirect cannot be followed safely")
                    return
                status = int(getattr(response, "status", 0))
                if status == 303 or (status in (301, 302) and method not in ("GET", "HEAD")):
                    method, body = ("HEAD" if method == "HEAD" else "GET"), None
                raw_headers = dict(getattr(request, "headers", None) or {})
                headers = {
                    k: v
                    for k, v in raw_headers.items()
                    if k.lower() not in _DROP_ON_HOP
                    and not (body is None and k.lower() == "content-type")
                }
                kwargs: dict[str, Any] = {
                    "method": method,
                    "headers": headers,
                    "max_redirects": 0,
                }
                if body is not None:
                    kwargs["data"] = body
                response = await _maybe_await(api.fetch(next_url, **kwargs))
                current_url = next_url
        except Exception as exc:
            await _abort(route, url, f"guarded fetch failed: {exc}")
            return

        await _maybe_await(route.fulfill(response=response))

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
    handler = make_route_guard(
        allowed_domains=allowed_domains,
        context=context,
        request_context=lambda: getattr(target, "request", None),
    )
    await _maybe_await(target.route("**/*", handler))
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
