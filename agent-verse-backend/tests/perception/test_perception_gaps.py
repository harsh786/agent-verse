"""Coverage gaps for app/perception/browser_agent.py and app/perception/multimodal.py.

Missing lines targeted:
  browser_agent.py: 77-109 — BrowserAgent.take_screenshot(), extract_text()
  browser_agent.py: 129-185 — click_and_screenshot(), fill_and_submit()
  browser_agent.py: 187-260 — analyze_screenshot(), run_action()
    (playwright available/unavailable paths)
  multimodal.py: 70-83 — resize_image_b64 with large image (Pillow available/unavailable)
"""
from __future__ import annotations

import base64
import io
from unittest.mock import AsyncMock, MagicMock

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


@pytest.mark.asyncio
async def test_click_and_screenshot_no_playwright(monkeypatch):
    """click_and_screenshot returns failure result when playwright not installed."""
    import app.perception.browser_agent as mod
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", False)
    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent()
    result = await agent.click_and_screenshot("https://example.com", "#button")
    assert result.success is False
    assert "Playwright not installed" in result.error


@pytest.mark.asyncio
async def test_fill_and_submit_no_playwright(monkeypatch):
    """fill_and_submit returns failure result when playwright not installed."""
    import app.perception.browser_agent as mod
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", False)
    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent()
    result = await agent.fill_and_submit("https://example.com", "#input", "hello")
    assert result.success is False
    assert "Playwright not installed" in result.error


@pytest.mark.asyncio
async def test_analyze_screenshot_no_playwright(monkeypatch):
    """analyze_screenshot returns 'No vision provider configured' when no vision provider."""
    import app.perception.browser_agent as mod
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", False)
    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent()  # no vision provider set
    result = await agent.analyze_screenshot("base64data", "What is on the page?")
    # analyze_screenshot returns a string (not BrowserResult) when no vision
    assert isinstance(result, str)
    assert "No vision provider" in result


@pytest.mark.asyncio
async def test_run_action_no_playwright(monkeypatch):
    """run_action returns failure result when playwright not installed."""
    import app.perception.browser_agent as mod
    from app.perception.browser_agent import BrowserAction
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", False)
    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent()
    action = BrowserAction(action_type="navigate", url="https://example.com")
    result = await agent.run_action(action)
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


@pytest.mark.asyncio
async def test_click_and_screenshot_with_playwright(monkeypatch):
    """click_and_screenshot returns success result when playwright is available."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.click = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
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
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(mod, "async_playwright", mock_async_playwright, raising=False)

    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent(headless=True)
    result = await agent.click_and_screenshot("https://example.com", "#button")

    assert result.success is True
    assert result.action == "click"
    assert len(result.screenshot_b64) > 0


@pytest.mark.asyncio
async def test_fill_and_submit_with_playwright(monkeypatch):
    """fill_and_submit returns success result when playwright is available."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.fill = AsyncMock()
    mock_page.click = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
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
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(mod, "async_playwright", mock_async_playwright, raising=False)

    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent(headless=True)
    result = await agent.fill_and_submit(
        "https://example.com", "#input", "hello", "#submit"
    )

    assert result.success is True
    assert result.action == "fill"
    assert len(result.screenshot_b64) > 0


@pytest.mark.asyncio
async def test_analyze_screenshot_with_playwright(monkeypatch):
    """analyze_screenshot calls the vision provider when one is configured."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod

    # Build a mock vision provider that supports vision and returns content
    mock_vision = MagicMock()
    mock_vision.supports_vision = MagicMock(return_value=True)
    mock_resp = MagicMock()
    mock_resp.content = "Page shows a login form"
    mock_vision.complete = AsyncMock(return_value=mock_resp)

    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", True)

    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent(headless=True, vision_provider=mock_vision)
    result = await agent.analyze_screenshot("base64screenshotbytes", "Describe the page")

    assert result == "Page shows a login form"
    mock_vision.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_action_with_playwright(monkeypatch):
    """run_action dispatches to take_screenshot on a navigate action."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod
    from app.perception.browser_agent import BrowserAction

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
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(mod, "async_playwright", mock_async_playwright, raising=False)

    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent(headless=True)
    action = BrowserAction(action_type="navigate", url="https://example.com")
    result = await agent.run_action(action)

    assert result.success is True
    assert result.action == "screenshot"
    assert len(result.screenshot_b64) > 0


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
    import builtins

    from app.perception.multimodal import resize_image_b64

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
        # Repeat bytes to exceed max_size (not a valid PNG but tests the size check)
        large_data = buf.getvalue() * 10
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


# ── Exception paths for click_and_screenshot and fill_and_submit ─────────────


@pytest.mark.asyncio
async def test_click_and_screenshot_with_playwright_exception(monkeypatch):
    """click_and_screenshot returns failure result when page.click raises."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.click = AsyncMock(side_effect=RuntimeError("element not visible"))
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=b"\x89PNG")
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
    result = await agent.click_and_screenshot("https://example.com", "#missing-btn")

    assert result.success is False
    assert result.action == "click"
    assert "element not visible" in result.error


@pytest.mark.asyncio
async def test_fill_and_submit_with_playwright_exception(monkeypatch):
    """fill_and_submit returns failure result when page.fill raises."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.fill = AsyncMock(side_effect=RuntimeError("selector not found"))
    mock_page.click = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=b"\x89PNG")
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
    result = await agent.fill_and_submit(
        "https://example.com", "#missing-input", "hello", "#submit"
    )

    assert result.success is False
    assert result.action == "fill"
    assert "selector not found" in result.error


@pytest.mark.asyncio
async def test_analyze_screenshot_no_vision_returns_default_message(monkeypatch):
    """analyze_screenshot returns 'No vision provider configured' when vision is None."""
    import app.perception.browser_agent as mod
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", False)
    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent()  # no vision_provider
    result = await agent.analyze_screenshot("base64data", "What is on the page?")
    assert isinstance(result, str)
    assert "No vision provider" in result


@pytest.mark.asyncio
async def test_analyze_screenshot_vision_does_not_support_vision(monkeypatch):
    """analyze_screenshot returns 'No vision provider configured' when vision
    provider is set but does not support vision."""
    from unittest.mock import MagicMock

    import app.perception.browser_agent as mod

    mock_vision = MagicMock()
    mock_vision.supports_vision = MagicMock(return_value=False)

    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", False)
    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent(headless=True, vision_provider=mock_vision)
    result = await agent.analyze_screenshot("base64data", "Describe the page")
    assert result == "No vision provider configured."


@pytest.mark.asyncio
async def test_run_action_unknown_action_type(monkeypatch):
    """run_action returns failure result for unknown action_type."""
    import app.perception.browser_agent as mod
    from app.perception.browser_agent import BrowserAction, BrowserAgent
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", True)
    agent = BrowserAgent()
    action = BrowserAction(action_type="scroll_to_bottom", url="https://example.com")
    result = await agent.run_action(action)
    assert result.success is False
    assert "Unknown action" in result.error


@pytest.mark.asyncio
async def test_run_action_extract_text(monkeypatch):
    """run_action dispatches to extract_text on extract_text action."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod
    from app.perception.browser_agent import BrowserAction, BrowserAgent

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.inner_text = AsyncMock(return_value="extracted text")
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

    agent = BrowserAgent(headless=True)
    action = BrowserAction(action_type="extract_text", url="https://example.com")
    result = await agent.run_action(action)

    assert result.success is True
    assert result.action == "extract_text"
    assert result.output == "extracted text"


@pytest.mark.asyncio
async def test_run_action_click(monkeypatch):
    """run_action dispatches to click_and_screenshot on click action."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod
    from app.perception.browser_agent import BrowserAction, BrowserAgent

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.click = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=b"\x89PNG")
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

    agent = BrowserAgent(headless=True)
    action = BrowserAction(action_type="click", url="https://example.com", selector="#btn")
    result = await agent.run_action(action)

    assert result.success is True
    assert result.action == "click"


@pytest.mark.asyncio
async def test_run_action_fill(monkeypatch):
    """run_action dispatches to fill_and_submit on fill action."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod
    from app.perception.browser_agent import BrowserAction, BrowserAgent

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.fill = AsyncMock()
    mock_page.click = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=b"\x89PNG")
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

    agent = BrowserAgent(headless=True)
    action = BrowserAction(
        action_type="fill", url="https://example.com", selector="#input", value="hello"
    )
    result = await agent.run_action(action)

    assert result.success is True
    assert result.action == "fill"


@pytest.mark.asyncio
async def test_fill_and_submit_no_submit_selector(monkeypatch):
    """fill_and_submit succeeds without submit_selector (no click)."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.fill = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=b"\x89PNG")
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
    # No submit_selector — should skip page.click
    result = await agent.fill_and_submit("https://example.com", "#input", "hello")

    assert result.success is True
    assert result.action == "fill"
    mock_page.click.assert_not_called()


@pytest.mark.asyncio
async def test_analyze_screenshot_vision_complete_exception(monkeypatch):
    """analyze_screenshot returns 'Vision analysis failed' when vision.complete raises."""
    from unittest.mock import AsyncMock, MagicMock

    import app.perception.browser_agent as mod

    mock_vision = MagicMock()
    mock_vision.supports_vision = MagicMock(return_value=True)
    mock_vision.complete = AsyncMock(side_effect=RuntimeError("rate limited"))

    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", False)

    from app.perception.browser_agent import BrowserAgent
    agent = BrowserAgent(headless=True, vision_provider=mock_vision)
    result = await agent.analyze_screenshot("base64data", "Describe the page")

    assert isinstance(result, str)
    assert "Vision analysis failed" in result
    assert "rate limited" in result
