"""Comprehensive tests for app/perception/browser_agent.py — covers all paths without real browser."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.perception.browser_agent as ba_module
from app.perception.browser_agent import BrowserAction, BrowserAgent, BrowserResult


# ── BrowserAction dataclass ───────────────────────────────────────────────────


def test_browser_action_defaults() -> None:
    action = BrowserAction(action_type="navigate")
    assert action.selector == ""
    assert action.value == ""
    assert action.url == ""


def test_browser_action_full() -> None:
    action = BrowserAction(
        action_type="fill",
        selector="#username",
        value="alice",
        url="https://login.example.com",
    )
    assert action.action_type == "fill"
    assert action.selector == "#username"
    assert action.value == "alice"


# ── BrowserResult dataclass ───────────────────────────────────────────────────


def test_browser_result_success() -> None:
    r = BrowserResult(success=True, action="screenshot", output="done", screenshot_b64="abc123")
    assert r.success is True
    assert r.error == ""


def test_browser_result_failure() -> None:
    r = BrowserResult(success=False, action="navigate", error="Timeout")
    assert r.success is False
    assert r.error == "Timeout"
    assert r.screenshot_b64 == ""


# ── BrowserAgent.available ────────────────────────────────────────────────────


def test_browser_agent_available_reflects_module_flag() -> None:
    original = ba_module._PLAYWRIGHT_AVAILABLE
    try:
        ba_module._PLAYWRIGHT_AVAILABLE = False
        agent = BrowserAgent()
        assert agent.available is False

        ba_module._PLAYWRIGHT_AVAILABLE = True
        agent2 = BrowserAgent()
        assert agent2.available is True
    finally:
        ba_module._PLAYWRIGHT_AVAILABLE = original


# ── All methods return graceful error when Playwright not installed ─────────────


async def test_take_screenshot_no_playwright() -> None:
    original = ba_module._PLAYWRIGHT_AVAILABLE
    ba_module._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.take_screenshot("https://example.com")
        assert result.success is False
        assert "Playwright" in result.error or "not installed" in result.error
        assert result.action == "screenshot"
    finally:
        ba_module._PLAYWRIGHT_AVAILABLE = original


async def test_extract_text_no_playwright() -> None:
    original = ba_module._PLAYWRIGHT_AVAILABLE
    ba_module._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.extract_text("https://example.com", "#main")
        assert result.success is False
        assert result.action == "extract_text"
    finally:
        ba_module._PLAYWRIGHT_AVAILABLE = original


async def test_click_and_screenshot_no_playwright() -> None:
    original = ba_module._PLAYWRIGHT_AVAILABLE
    ba_module._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.click_and_screenshot("https://example.com", "#btn")
        assert result.success is False
        assert result.action == "click"
    finally:
        ba_module._PLAYWRIGHT_AVAILABLE = original


async def test_fill_and_submit_no_playwright() -> None:
    original = ba_module._PLAYWRIGHT_AVAILABLE
    ba_module._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.fill_and_submit(
            "https://example.com", "#q", "search term", "#go"
        )
        assert result.success is False
        assert result.action == "fill"
    finally:
        ba_module._PLAYWRIGHT_AVAILABLE = original


async def test_fill_and_submit_no_submit_selector_no_playwright() -> None:
    original = ba_module._PLAYWRIGHT_AVAILABLE
    ba_module._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.fill_and_submit("https://example.com", "#field", "value")
        assert result.success is False
    finally:
        ba_module._PLAYWRIGHT_AVAILABLE = original


# ── analyze_screenshot ────────────────────────────────────────────────────────


async def test_analyze_screenshot_no_vision_provider() -> None:
    agent = BrowserAgent(vision_provider=None)
    result = await agent.analyze_screenshot("b64data", "What is this?")
    assert "No vision provider" in result


async def test_analyze_screenshot_vision_provider_no_supports_vision() -> None:
    mock_vision = MagicMock()
    mock_vision.supports_vision = MagicMock(return_value=False)
    agent = BrowserAgent(vision_provider=mock_vision)
    result = await agent.analyze_screenshot("b64data", "Describe this page")
    assert "No vision provider" in result


async def test_analyze_screenshot_with_vision_returns_content() -> None:
    from app.providers.base import CompletionResponse

    mock_vision = MagicMock()
    mock_vision.supports_vision = MagicMock(return_value=True)
    mock_resp = MagicMock()
    mock_resp.content = "A login page with username and password fields."
    mock_vision.complete = AsyncMock(return_value=mock_resp)

    agent = BrowserAgent(vision_provider=mock_vision)
    result = await agent.analyze_screenshot("base64encodedimage", "What is on this page?")
    assert "login page" in result.lower()
    mock_vision.complete.assert_called_once()


async def test_analyze_screenshot_vision_exception_returns_error_string() -> None:
    mock_vision = MagicMock()
    mock_vision.supports_vision = MagicMock(return_value=True)
    mock_vision.complete = AsyncMock(side_effect=RuntimeError("API error"))

    agent = BrowserAgent(vision_provider=mock_vision)
    result = await agent.analyze_screenshot("b64data", "question")
    assert "Vision analysis failed" in result


# ── run_action dispatch ───────────────────────────────────────────────────────


async def test_run_action_navigate_calls_take_screenshot() -> None:
    original = ba_module._PLAYWRIGHT_AVAILABLE
    ba_module._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.run_action(
            BrowserAction(action_type="navigate", url="https://example.com")
        )
        assert result.action == "screenshot"
    finally:
        ba_module._PLAYWRIGHT_AVAILABLE = original


async def test_run_action_screenshot_calls_take_screenshot() -> None:
    original = ba_module._PLAYWRIGHT_AVAILABLE
    ba_module._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.run_action(
            BrowserAction(action_type="screenshot", url="https://example.com")
        )
        assert result.action == "screenshot"
    finally:
        ba_module._PLAYWRIGHT_AVAILABLE = original


async def test_run_action_extract_text_dispatches() -> None:
    original = ba_module._PLAYWRIGHT_AVAILABLE
    ba_module._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.run_action(
            BrowserAction(action_type="extract_text", url="https://example.com", selector="#main")
        )
        assert result.action == "extract_text"
        assert result.success is False
    finally:
        ba_module._PLAYWRIGHT_AVAILABLE = original


async def test_run_action_fill_dispatches() -> None:
    original = ba_module._PLAYWRIGHT_AVAILABLE
    ba_module._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        result = await agent.run_action(
            BrowserAction(
                action_type="fill",
                url="https://example.com",
                selector="#q",
                value="test",
            )
        )
        assert result.action == "fill"
    finally:
        ba_module._PLAYWRIGHT_AVAILABLE = original


async def test_run_action_unknown_type_returns_error() -> None:
    agent = BrowserAgent()
    result = await agent.run_action(
        BrowserAction(action_type="teleport", url="https://warp.com")
    )
    assert result.success is False
    assert "Unknown action" in result.error


# ── BrowserAgent instantiation ────────────────────────────────────────────────


def test_browser_agent_timeout_and_headless_params() -> None:
    agent = BrowserAgent(timeout_ms=5000, headless=False)
    assert agent._timeout == 5000
    assert agent._headless is False


def test_browser_agent_default_timeout() -> None:
    agent = BrowserAgent()
    assert agent._timeout == 30_000
    assert agent._headless is True
