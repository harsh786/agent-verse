"""WS-13: RPAExecutor real-HTTP fallback (browser-less REAL page text).

When Playwright is not installed, ``rpa_open_url``/``rpa_extract_text`` must fetch
the page over httpx and return its REAL text (not a ``[simulated]`` placeholder)
when a caller opts in via ``allow_http_fetch=True`` — so routing the KB scraper
through the executor no longer regresses the no-browser path. With the flag off,
the existing simulation behaviour is preserved.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rpa.executor import RPAExecutor

_HTML = (
    "<html><head><title>Real Title</title></head><body><h1>Hello</h1>"
    "<p>Real world content here.</p><script>ignored()</script></body></html>"
)


def _sim_executor() -> RPAExecutor:
    ex = RPAExecutor()
    ex._playwright_available = False  # force browser-less path
    return ex


def _mock_httpx(text: str) -> Any:
    resp = MagicMock()
    resp.text = text
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=ctx)
    ctx.__aexit__ = AsyncMock(return_value=False)
    ctx.get = AsyncMock(return_value=resp)
    client = MagicMock(return_value=ctx)
    return client


@pytest.mark.asyncio
async def test_open_then_extract_returns_real_text() -> None:
    ex = _sim_executor()
    with patch("httpx.AsyncClient", _mock_httpx(_HTML)):
        opened = await ex.execute(
            tool_name="rpa_open_url",
            arguments={"url": "https://1.1.1.1"},
            session_id="s1",
            allow_http_fetch=True,
        )
        extracted = await ex.execute(
            tool_name="rpa_extract_text",
            arguments={},
            session_id="s1",
            allow_http_fetch=True,
        )
    assert opened.success and "Real Title" in opened.output
    assert extracted.success
    assert "Real world content" in extracted.output
    assert "[simulated]" not in extracted.output
    assert "ignored()" not in extracted.output  # script body stripped


@pytest.mark.asyncio
async def test_flag_off_keeps_simulation() -> None:
    ex = _sim_executor()
    # No allow_http_fetch → the classic simulation placeholder (no network call).
    result = await ex.execute(
        tool_name="rpa_extract_text", arguments={"selector": ".x"}
    )
    assert result.success
    assert "[simulated]" in result.output


@pytest.mark.asyncio
async def test_extract_with_inline_url_fetches_directly() -> None:
    ex = _sim_executor()
    with patch("httpx.AsyncClient", _mock_httpx(_HTML)):
        extracted = await ex.execute(
            tool_name="rpa_extract_text",
            arguments={"url": "https://1.1.1.1"},
            session_id="s2",
            allow_http_fetch=True,
        )
    assert "Real world content" in extracted.output


@pytest.mark.asyncio
async def test_open_url_fetch_error_is_reported() -> None:
    ex = _sim_executor()

    def _boom(*args: Any, **kwargs: Any) -> Any:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=False)
        ctx.get = AsyncMock(side_effect=RuntimeError("connect failed"))
        return ctx

    with patch("httpx.AsyncClient", _boom):
        result = await ex.execute(
            tool_name="rpa_open_url",
            arguments={"url": "https://1.1.1.1"},
            session_id="s3",
            allow_http_fetch=True,
        )
    assert result.success is False
    assert "connect failed" in (result.error or "")


@pytest.mark.asyncio
async def test_screenshot_is_graceful_noop_over_httpx() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_screenshot",
        arguments={"name": "page"},
        session_id="s4",
        allow_http_fetch=True,
    )
    assert result.success is True
    assert result.artifact_url is None
