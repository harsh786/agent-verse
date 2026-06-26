"""Tests for BrowserAgent (no real browser needed — tests graceful degradation)."""
from __future__ import annotations

import pytest

from app.perception.browser_agent import BrowserAgent, BrowserAction, BrowserResult


def test_browser_agent_reports_availability() -> None:
    agent = BrowserAgent()
    # available is True if playwright is installed, False otherwise
    assert isinstance(agent.available, bool)


async def test_screenshot_without_playwright_returns_error() -> None:
    """When Playwright is not installed, screenshot returns graceful error."""
    import app.perception.browser_agent as ba  # noqa: PLC0415

    original = ba._PLAYWRIGHT_AVAILABLE
    ba._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.take_screenshot("http://example.com")
        assert result.success is False
        assert "Playwright" in result.error or "not installed" in result.error
    finally:
        ba._PLAYWRIGHT_AVAILABLE = original


async def test_extract_text_without_playwright_returns_error() -> None:
    import app.perception.browser_agent as ba  # noqa: PLC0415

    original = ba._PLAYWRIGHT_AVAILABLE
    ba._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.extract_text("http://example.com")
        assert result.success is False
    finally:
        ba._PLAYWRIGHT_AVAILABLE = original


async def test_run_action_unknown_type() -> None:
    import app.perception.browser_agent as ba  # noqa: PLC0415

    original = ba._PLAYWRIGHT_AVAILABLE
    ba._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.run_action(BrowserAction(action_type="unknown", url="http://x.com"))
        assert result.success is False
    finally:
        ba._PLAYWRIGHT_AVAILABLE = original


async def test_analyze_screenshot_without_vision_provider() -> None:
    agent = BrowserAgent(vision_provider=None)
    result = await agent.analyze_screenshot("base64data", "What is on screen?")
    assert "No vision provider" in result


async def test_run_action_screenshot_dispatches() -> None:
    """navigate and screenshot action types both call take_screenshot."""
    import app.perception.browser_agent as ba  # noqa: PLC0415

    original = ba._PLAYWRIGHT_AVAILABLE
    ba._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        for action_type in ("navigate", "screenshot"):
            result = await agent.run_action(
                BrowserAction(action_type=action_type, url="http://x.com")
            )
            assert result.success is False
            assert result.action == "screenshot"
    finally:
        ba._PLAYWRIGHT_AVAILABLE = original


async def test_run_action_click_dispatches() -> None:
    import app.perception.browser_agent as ba  # noqa: PLC0415

    original = ba._PLAYWRIGHT_AVAILABLE
    ba._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.run_action(
            BrowserAction(action_type="click", url="http://x.com", selector="#btn")
        )
        assert result.success is False
        assert result.action == "click"
    finally:
        ba._PLAYWRIGHT_AVAILABLE = original


async def test_run_action_fill_dispatches() -> None:
    import app.perception.browser_agent as ba  # noqa: PLC0415

    original = ba._PLAYWRIGHT_AVAILABLE
    ba._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.run_action(
            BrowserAction(action_type="fill", url="http://x.com", selector="#q", value="hello")
        )
        assert result.success is False
        assert result.action == "fill"
    finally:
        ba._PLAYWRIGHT_AVAILABLE = original
