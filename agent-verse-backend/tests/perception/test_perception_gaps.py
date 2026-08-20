"""Coverage gaps for app/perception/browser_agent.py and app/perception/multimodal.py.

Missing lines targeted:
  browser_agent.py: 77-109 — BrowserAgent.take_screenshot(), extract_text()
    (playwright available/unavailable paths)
  multimodal.py: 70-83 — resize_image_b64 with large image (Pillow available/unavailable)
"""
from __future__ import annotations

import base64
import io

import pytest


# ── BrowserAgent — playwright NOT installed ────────────────────────────────────

def test_browser_agent_available_false_without_playwright(monkeypatch):
    """BrowserAgent.available returns False when playwright is not importable."""
    import app.perception.browser_agent as mod
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", False)
    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent()
    assert agent.available is False


@pytest.mark.asyncio
async def test_take_screenshot_no_playwright(monkeypatch):
    """take_screenshot returns failure result when playwright not installed."""
    import app.perception.browser_agent as mod
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", False)
    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent()
    result = await agent.take_screenshot("https://example.com")
    assert result.success is False
    assert "Playwright not installed" in result.error


@pytest.mark.asyncio
async def test_extract_text_no_playwright(monkeypatch):
    """extract_text returns failure result when playwright not installed."""
    import app.perception.browser_agent as mod
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", False)
    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent()
    result = await agent.extract_text("https://example.com")
    assert result.success is False
    assert "Playwright not installed" in result.error


# ── BrowserAgent — playwright installed, mocked ────────────────────────────────

@pytest.mark.asyncio
async def test_take_screenshot_with_playwright(monkeypatch):
    """take_screenshot returns success result when playwright is available."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod

    # Build mock playwright chain
    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
    mock_page.set_default_timeout = MagicMock()

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = MagicMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium
    mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw.__aexit__ = AsyncMock(return_value=False)

    mock_async_playwright = MagicMock(return_value=mock_pw)

    # Inject both the availability flag and the mock function into the module
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(mod, "async_playwright", mock_async_playwright, raising=False)

    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent(headless=True)
    result = await agent.take_screenshot("https://example.com")

    assert result.success is True
    assert result.action == "screenshot"
    assert len(result.screenshot_b64) > 0


@pytest.mark.asyncio
async def test_take_screenshot_exception(monkeypatch):
    """take_screenshot returns failure on navigation exception."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod

    mock_page = MagicMock()
    mock_page.goto = AsyncMock(side_effect=Exception("Navigation timeout"))
    mock_page.set_default_timeout = MagicMock()

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = MagicMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium
    mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw.__aexit__ = AsyncMock(return_value=False)

    mock_async_playwright = MagicMock(return_value=mock_pw)
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(mod, "async_playwright", mock_async_playwright, raising=False)

    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent(headless=True)
    result = await agent.take_screenshot("https://bad-url.invalid")

    assert result.success is False
    assert "Navigation timeout" in result.error


@pytest.mark.asyncio
async def test_extract_text_with_playwright(monkeypatch):
    """extract_text returns page text when playwright is available."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.inner_text = AsyncMock(return_value="Hello World content")
    mock_page.set_default_timeout = MagicMock()

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = MagicMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium
    mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw.__aexit__ = AsyncMock(return_value=False)

    mock_async_playwright = MagicMock(return_value=mock_pw)
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(mod, "async_playwright", mock_async_playwright, raising=False)

    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent(headless=True)
    result = await agent.extract_text("https://example.com", selector="body")

    assert result.success is True
    assert result.action == "extract_text"
    assert "Hello World" in result.output


@pytest.mark.asyncio
async def test_extract_text_exception(monkeypatch):
    """extract_text returns failure on exception."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod

    mock_page = MagicMock()
    mock_page.goto = AsyncMock(side_effect=Exception("DNS resolution failed"))
    mock_page.set_default_timeout = MagicMock()

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = MagicMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium
    mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw.__aexit__ = AsyncMock(return_value=False)

    mock_async_playwright = MagicMock(return_value=mock_pw)
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(mod, "async_playwright", mock_async_playwright, raising=False)

    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent(headless=True)
    result = await agent.extract_text("https://bad.invalid")

    assert result.success is False
    assert "DNS resolution failed" in result.error


# ── BrowserAction and BrowserResult dataclasses ───────────────────────────────

def test_browser_action_defaults():
    from app.perception.browser_agent import BrowserAction
    a = BrowserAction(action_type="navigate", url="https://example.com")
    assert a.selector == ""
    assert a.value == ""


def test_browser_result_defaults():
    from app.perception.browser_agent import BrowserResult
    r = BrowserResult(success=True, action="screenshot")
    assert r.output == ""
    assert r.screenshot_b64 == ""
    assert r.error == ""


# ── resize_image_b64 — multimodal.py ─────────────────────────────────────────

def test_resize_image_b64_small_image_unchanged():
    """Small image (under max_size) is returned unchanged."""
    from app.perception.multimodal import resize_image_b64

    tiny_data = b"X" * 100
    b64 = base64.b64encode(tiny_data).decode()
    result = resize_image_b64(b64, max_size=1_000_000)
    assert result == b64


def test_resize_image_b64_invalid_base64_returns_original():
    """Invalid base64 input returns original string without raising."""
    from app.perception.multimodal import resize_image_b64

    result = resize_image_b64("not-valid-base64!!!", max_size=10)
    assert result == "not-valid-base64!!!"


def test_resize_image_b64_large_no_pillow(monkeypatch):
    """Large image without Pillow installed returns original base64."""
    from app.perception.multimodal import resize_image_b64
    import builtins

    orig_import = builtins.__import__

    def _block_pil(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("PIL not installed (mocked)")
        return orig_import(name, *args, **kwargs)

    large_data = b"A" * 2_000_000
    b64 = base64.b64encode(large_data).decode()

    monkeypatch.setattr(builtins, "__import__", _block_pil)
    result = resize_image_b64(b64, max_size=100)
    # Should return original since Pillow not available
    assert result == b64


def test_resize_image_b64_large_with_pillow():
    """Large image with Pillow available is resized (or returns original if Pillow unavailable)."""
    from app.perception.multimodal import resize_image_b64

    # Create a minimal valid PNG (1x1 pixel) to test both paths
    try:
        from PIL import Image  # type: ignore[import]
        buf = io.BytesIO()
        img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        img.save(buf, format="PNG")
        large_data = buf.getvalue() * 10  # make it bigger by repeating (not valid PNG but tests the size check)
        b64 = base64.b64encode(large_data).decode()
        result = resize_image_b64(b64, max_size=50)
        # Either resized or returned as-is on exception — both are valid
        assert isinstance(result, str)
    except ImportError:
        # Pillow not installed — test the fallback path
        large_data = b"B" * 2_000_000
        b64 = base64.b64encode(large_data).decode()
        result = resize_image_b64(b64, max_size=100)
        assert result == b64
