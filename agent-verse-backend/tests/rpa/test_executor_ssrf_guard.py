"""SSRF egress guard on the RPA executor.

An agent's ``rpa_*`` tool calls drive a real headless browser (page.goto) or,
under the WS-13 http fallback, a real HTTP GET. Without a guard, an
attacker-supplied ``url`` could reach the cloud metadata endpoint, loopback, or
RFC-1918 hosts (SSRF). These tests prove the executor blocks those targets
*before* any fetch, allows genuinely public targets, and honours an explicit
per-executor domain allowlist.

Playwright is not installed in the test env, so the http-fallback path
(``allow_http_fetch=True``) is used to exercise the same central guard in
``RPAExecutor.execute``. ``httpx.AsyncClient`` is patched with a sentinel that
raises if it is ever constructed — proving a blocked URL never reaches the
network.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.rpa.executor import RPAExecutor


def _explode_httpx() -> Any:
    """A fake httpx.AsyncClient that fails the test if instantiated at all."""

    def _factory(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("httpx must NOT be called for an SSRF-blocked URL")

    return _factory


def _ok_httpx(html: str = "<html><body>public</body></html>") -> Any:
    def _factory(*args: Any, **kwargs: Any) -> Any:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=False)
        resp = AsyncMock()
        resp.text = html
        resp.raise_for_status = lambda: None
        ctx.get = AsyncMock(return_value=resp)
        return ctx

    return _factory


BLOCKED_URLS = [
    "http://169.254.169.254/latest/meta-data/",  # AWS/GCP/Azure metadata
    "http://metadata.google.internal/computeMetadata/v1/",  # GCP metadata host
    "http://127.0.0.1:8000/admin",  # loopback
    "http://localhost:6379/",  # loopback via name
    "http://10.0.0.5/internal",  # RFC-1918
    "http://192.168.1.1/router",  # RFC-1918
    "file:///etc/passwd",  # non-http scheme
    "gopher://127.0.0.1:6379/_INFO",  # non-http scheme
]


@pytest.mark.parametrize("url", BLOCKED_URLS)
@pytest.mark.asyncio
async def test_open_url_blocks_internal_targets(url: str) -> None:
    ex = RPAExecutor()
    with patch("httpx.AsyncClient", _explode_httpx()):
        result = await ex.execute(
            tool_name="rpa_open_url",
            arguments={"url": url},
            session_id="s-block",
            allow_http_fetch=True,
        )
    assert result.success is False
    assert "SSRF guard" in (result.error or "")


@pytest.mark.asyncio
async def test_extract_text_inline_url_is_also_guarded() -> None:
    """The guard is central to execute(), so every url-bearing tool is covered."""
    ex = RPAExecutor()
    with patch("httpx.AsyncClient", _explode_httpx()):
        result = await ex.execute(
            tool_name="rpa_extract_text",
            arguments={"url": "http://169.254.169.254/latest/meta-data/"},
            session_id="s-block2",
            allow_http_fetch=True,
        )
    assert result.success is False
    assert "SSRF guard" in (result.error or "")


@pytest.mark.asyncio
async def test_public_url_passes_the_guard() -> None:
    """A literal public IP (no DNS needed) reaches the fetch path normally."""
    ex = RPAExecutor()
    with patch("httpx.AsyncClient", _ok_httpx()):
        result = await ex.execute(
            tool_name="rpa_open_url",
            arguments={"url": "https://1.1.1.1/"},
            session_id="s-ok",
            allow_http_fetch=True,
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_simulation_path_is_exempt_from_the_guard() -> None:
    """Without allow_http_fetch (and no browser) nothing is fetched, so an
    internal-looking URL is harmless and must not be blocked — it stays a
    simulation placeholder."""
    ex = RPAExecutor()
    result = await ex.execute(
        tool_name="rpa_open_url",
        arguments={"url": "http://127.0.0.1/whatever"},
        session_id="s-sim",
    )
    assert result.success is True
    assert "SSRF guard" not in (result.error or "")


@pytest.mark.asyncio
async def test_allowlist_permits_an_internal_host_but_still_blocks_others() -> None:
    """An explicit allowlist is the sanctioned override for e.g. an internal
    staging host; hosts not on the list stay blocked."""
    ex = RPAExecutor(allowed_domains=["internal.example"])
    with patch("httpx.AsyncClient", _ok_httpx()):
        allowed = await ex.execute(
            tool_name="rpa_open_url",
            arguments={"url": "http://internal.example/dashboard"},
            session_id="s-allow",
            allow_http_fetch=True,
        )
    assert allowed.success is True

    # A different internal host is NOT covered by the allowlist → still blocked.
    with patch("httpx.AsyncClient", _explode_httpx()):
        blocked = await ex.execute(
            tool_name="rpa_open_url",
            arguments={"url": "http://169.254.169.254/latest/meta-data/"},
            session_id="s-allow2",
            allow_http_fetch=True,
        )
    assert blocked.success is False
    assert "SSRF guard" in (blocked.error or "")
