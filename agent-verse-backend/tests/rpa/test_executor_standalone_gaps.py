"""Coverage gaps for app/rpa/executor.py.

Missing lines targeted:
  410-450 — _execute_playwright_standalone: all tool branches when playwright available
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rpa.executor import RPAExecutor, RPAResult

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_mock_playwright(
    title: str = "Test Page",
    screenshot: bytes = b"\x89PNG\r\n",
    inner_text: str = "Page content here",
    content: str = "<html><body>Test</body></html>",
):
    """Build a full playwright mock stack (pw → chromium → browser → context → page)."""
    page = MagicMock()
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value=title)
    page.screenshot = AsyncMock(return_value=screenshot)
    page.inner_text = AsyncMock(return_value=inner_text)
    page.content = AsyncMock(return_value=content)
    page.click = AsyncMock()
    page.get_by_text = MagicMock(return_value=MagicMock(
        first=MagicMock(click=AsyncMock())
    ))
    page.fill = AsyncMock()
    page.select_option = AsyncMock(return_value=["opt1"])
    page.evaluate = AsyncMock(return_value=None)
    page.set_default_timeout = MagicMock()

    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)

    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    chromium = MagicMock()
    chromium.launch = AsyncMock(return_value=browser)

    pw = MagicMock()
    pw.chromium = chromium
    pw.__aenter__ = AsyncMock(return_value=pw)
    pw.__aexit__ = AsyncMock(return_value=False)

    return pw, page


def _make_playwright_sys_modules(pw, page):
    """Inject a fake playwright package into sys.modules so the executor can import it."""
    mock_api = MagicMock()
    mock_api.async_playwright = MagicMock(return_value=pw)
    return {"playwright": mock_api, "playwright.async_api": mock_api}


# ── _execute_playwright_standalone: no playwright (fallback to simulation) ────

@pytest.mark.asyncio
async def test_standalone_falls_back_to_simulation_when_no_playwright():
    """When playwright can't be imported, executor._playwright_available=False → simulation."""
    executor = RPAExecutor(headless=True)
    executor._playwright_available = False

    result = await executor._execute_playwright_standalone(
        tool_name="rpa_open_url",
        arguments={"url": "https://example.com"},
    )
    assert result.success is True  # simulation always succeeds


# ── _execute_playwright_standalone: rpa_open_url ─────────────────────────────

@pytest.mark.asyncio
async def test_standalone_rpa_open_url():
    """rpa_open_url navigates and returns page title."""
    executor = RPAExecutor(headless=True)
    executor._playwright_available = True

    pw, _ = _make_mock_playwright(title="Example Domain")
    mods = _make_playwright_sys_modules(pw, _)

    with patch.dict(sys.modules, mods):
        result = await executor._execute_playwright_standalone(
            tool_name="rpa_open_url",
            arguments={"url": "https://example.com"},
        )

    assert result.success is True
    assert "Example Domain" in result.output


@pytest.mark.asyncio
async def test_standalone_rpa_open_url_no_url():
    """rpa_open_url without url argument returns failure."""
    executor = RPAExecutor(headless=True)
    executor._playwright_available = True

    pw, _ = _make_mock_playwright()
    mods = _make_playwright_sys_modules(pw, _)

    with patch.dict(sys.modules, mods):
        result = await executor._execute_playwright_standalone(
            tool_name="rpa_open_url",
            arguments={},
        )

    assert result.success is False
    assert "url" in result.error.lower()


# ── _execute_playwright_standalone: rpa_screenshot ────────────────────────────

@pytest.mark.asyncio
async def test_standalone_rpa_screenshot():
    """rpa_screenshot takes screenshot and returns base64 artifact."""
    executor = RPAExecutor(headless=True)
    executor._playwright_available = True

    screenshot_bytes = b"\x89PNG\r\n\x1a\nSOMEDATA"
    pw, _ = _make_mock_playwright(screenshot=screenshot_bytes)
    mods = _make_playwright_sys_modules(pw, _)

    with patch.dict(sys.modules, mods):
        result = await executor._execute_playwright_standalone(
            tool_name="rpa_screenshot",
            arguments={"url": "https://example.com"},
        )

    assert result.success is True
    assert result.artifact_url is not None
    assert len(result.artifact_url) > 0


# ── _execute_playwright_standalone: rpa_extract_text ─────────────────────────

@pytest.mark.asyncio
async def test_standalone_rpa_extract_text():
    """rpa_extract_text returns page inner text."""
    executor = RPAExecutor(headless=True)
    executor._playwright_available = True

    pw, _ = _make_mock_playwright(inner_text="Important page content")
    mods = _make_playwright_sys_modules(pw, _)

    with patch.dict(sys.modules, mods):
        result = await executor._execute_playwright_standalone(
            tool_name="rpa_extract_text",
            arguments={"url": "https://example.com", "selector": "body"},
        )

    assert result.success is True
    assert "Important page content" in result.output


# ── _execute_playwright_standalone: rpa_click ────────────────────────────────

@pytest.mark.asyncio
async def test_standalone_rpa_click_by_selector():
    """rpa_click by CSS selector succeeds."""
    executor = RPAExecutor(headless=True)
    executor._playwright_available = True

    pw, page = _make_mock_playwright()
    mods = _make_playwright_sys_modules(pw, page)

    with patch.dict(sys.modules, mods):
        result = await executor._execute_playwright_standalone(
            tool_name="rpa_click",
            arguments={"url": "https://example.com", "selector": "#submit-btn"},
        )

    assert result.success is True
    page.click.assert_called_once()


@pytest.mark.asyncio
async def test_standalone_rpa_click_no_selector_no_text():
    """rpa_click without selector or text returns failure."""
    executor = RPAExecutor(headless=True)
    executor._playwright_available = True

    pw, _ = _make_mock_playwright()
    mods = _make_playwright_sys_modules(pw, _)

    with patch.dict(sys.modules, mods):
        result = await executor._execute_playwright_standalone(
            tool_name="rpa_click",
            arguments={},
        )

    assert result.success is False


# ── _execute_playwright_standalone: rpa_type ─────────────────────────────────

@pytest.mark.asyncio
async def test_standalone_rpa_type():
    """rpa_type fills a field by typing text and returns success."""
    executor = RPAExecutor(headless=True)
    executor._playwright_available = True

    pw, page = _make_mock_playwright()
    # rpa_type uses page.fill or page.type
    page.fill = AsyncMock()
    mods = _make_playwright_sys_modules(pw, page)

    with patch.dict(sys.modules, mods):
        result = await executor._execute_playwright_standalone(
            tool_name="rpa_type",
            arguments={
                "url": "https://example.com",
                "selector": "#email",
                "value": "user@example.com",
            },
        )

    assert result.success is True


# ── execute() routes to standalone when no session_manager ───────────────────

@pytest.mark.asyncio
async def test_execute_routes_to_standalone_no_session_manager():
    """execute() uses standalone path when playwright available but no session_manager."""
    executor = RPAExecutor(headless=True, session_manager=None)
    executor._playwright_available = True

    with patch.object(
        executor,
        "_execute_playwright_standalone",
        new_callable=AsyncMock,
        return_value=RPAResult(success=True, output="standalone called"),
    ) as mock_standalone:
        result = await executor.execute(
            tool_name="rpa_open_url",
            arguments={"url": "https://example.com"},
        )

    mock_standalone.assert_called_once()
    assert result.success is True
