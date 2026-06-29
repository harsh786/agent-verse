"""Comprehensive coverage tests for app/rpa/executor.py.

Forces _playwright_available=False so no browser is needed.
Covers RPAResult, _execute_simulation (all tools), execute() dispatch,
credential injection, and edge cases.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.rpa.executor import RPAExecutor, RPAResult


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def executor():
    """RPAExecutor with Playwright forcibly disabled."""
    ex = RPAExecutor()
    ex._playwright_available = False
    return ex


# ── RPAResult dataclass ───────────────────────────────────────────────────────

def test_rpa_result_success_defaults():
    r = RPAResult(success=True)
    assert r.success is True
    assert r.output == ""
    assert r.artifact_url is None
    assert r.artifact_name is None
    assert r.duration_ms == 0.0
    assert r.error is None


def test_rpa_result_failure():
    r = RPAResult(success=False, error="something went wrong")
    assert r.success is False
    assert r.error == "something went wrong"


def test_rpa_result_with_artifact():
    r = RPAResult(
        success=True,
        output="screenshot taken",
        artifact_url="data:image/png;base64,abc",
        artifact_name="page.png",
        duration_ms=123.4,
    )
    assert r.artifact_url == "data:image/png;base64,abc"
    assert r.artifact_name == "page.png"
    assert r.duration_ms == 123.4


# ── _execute_simulation: all standard tools ──────────────────────────────────

@pytest.mark.asyncio
async def test_simulate_open_url(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_open_url", arguments={"url": "https://example.com"}
    )
    assert r.success is True
    assert "example.com" in r.output


@pytest.mark.asyncio
async def test_simulate_click_with_selector(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_click", arguments={"selector": "#submit-btn"}
    )
    assert r.success is True
    assert "#submit-btn" in r.output


@pytest.mark.asyncio
async def test_simulate_click_with_text(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_click", arguments={"text": "Login"}
    )
    assert r.success is True
    assert "Login" in r.output


@pytest.mark.asyncio
async def test_simulate_type(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_type",
        arguments={"selector": "#email", "text": "hello@world.com"},
    )
    assert r.success is True
    assert "hello@world.com" in r.output


@pytest.mark.asyncio
async def test_simulate_extract_text(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_extract_text", arguments={"selector": "#content"}
    )
    assert r.success is True
    assert "#content" in r.output


@pytest.mark.asyncio
async def test_simulate_screenshot(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_screenshot", arguments={"name": "dashboard_snap"}
    )
    assert r.success is True
    assert "dashboard_snap" in r.output


@pytest.mark.asyncio
async def test_simulate_wait_for_text(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_wait_for_text", arguments={"text": "Welcome back!"}
    )
    assert r.success is True
    assert "Welcome back!" in r.output


@pytest.mark.asyncio
async def test_simulate_select_option(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_select_option",
        arguments={"selector": "#country", "value": "US"},
    )
    assert r.success is True
    assert "US" in r.output
    assert "#country" in r.output


@pytest.mark.asyncio
async def test_simulate_upload_file(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_upload_file",
        arguments={"selector": "#file-input", "file_path": "/tmp/report.csv"},
    )
    assert r.success is True
    assert "/tmp/report.csv" in r.output


@pytest.mark.asyncio
async def test_simulate_download_file(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_download_file", arguments={"selector": "#dl-link"}
    )
    assert r.success is True
    assert "#dl-link" in r.output


@pytest.mark.asyncio
async def test_simulate_submit_form_counts_fields(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_submit_form",
        arguments={
            "field_values": {
                "#name": "Alice",
                "#email": "alice@example.com",
                "#phone": "555-1234",
            }
        },
    )
    assert r.success is True
    assert "3" in r.output


@pytest.mark.asyncio
async def test_simulate_submit_form_empty_fields(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_submit_form", arguments={}
    )
    assert r.success is True
    assert "0" in r.output


# ── _execute_simulation: P1.2 extended tools ─────────────────────────────────

@pytest.mark.asyncio
async def test_simulate_detect_captcha(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_detect_captcha", arguments={}
    )
    assert r.success is True
    assert "captcha_detected" in r.output


@pytest.mark.asyncio
async def test_simulate_request_human_help(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_request_human_help",
        arguments={"reason": "CAPTCHA cannot be solved automatically"},
    )
    assert r.success is True
    assert "CAPTCHA cannot be solved" in r.output
    assert "takeover" in r.output.lower() or "/rpa/live" in r.output


@pytest.mark.asyncio
async def test_simulate_request_human_help_default_reason(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_request_human_help", arguments={}
    )
    assert r.success is True
    assert "Assistance required" in r.output


@pytest.mark.asyncio
async def test_simulate_wait_for_network_idle(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_wait_for_network_idle", arguments={"timeout_ms": 5000}
    )
    assert r.success is True
    assert "5000" in r.output


@pytest.mark.asyncio
async def test_simulate_wait_for_network_idle_default_timeout(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_wait_for_network_idle", arguments={}
    )
    assert r.success is True
    assert "10000" in r.output  # default 10000ms


@pytest.mark.asyncio
async def test_simulate_unknown_tool(executor):
    r = await executor._execute_simulation(
        tool_name="rpa_does_not_exist", arguments={}
    )
    assert r.success is False
    assert "Unknown RPA tool" in (r.error or "")


# ── execute() top-level dispatch ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_dispatches_to_simulation(executor):
    r = await executor.execute(
        tool_name="rpa_open_url",
        arguments={"url": "https://test.example.com"},
    )
    assert r.success is True
    assert r.duration_ms >= 0


@pytest.mark.asyncio
async def test_execute_sets_duration_ms(executor):
    r = await executor.execute(
        tool_name="rpa_screenshot", arguments={"name": "snap"}
    )
    assert r.duration_ms > 0  # simulation adds asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_execute_ephemeral_session_calls_close():
    """Ephemeral sessions (no session_id given) are closed via session_manager."""
    ex = RPAExecutor()
    ex._playwright_available = False

    mock_sm = AsyncMock()
    mock_sm.close = AsyncMock()
    ex._session_manager = mock_sm

    r = await ex.execute(
        tool_name="rpa_screenshot",
        arguments={"name": "snap"},
        # session_id=None (default) → ephemeral
    )
    assert r.success is True
    mock_sm.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_non_ephemeral_session_no_close():
    """Provided session_id → NOT cleaned up automatically."""
    ex = RPAExecutor()
    ex._playwright_available = False

    mock_sm = AsyncMock()
    mock_sm.close = AsyncMock()
    ex._session_manager = mock_sm

    r = await ex.execute(
        tool_name="rpa_open_url",
        arguments={"url": "http://a.com"},
        session_id="existing-session-id",
    )
    assert r.success is True
    mock_sm.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_with_credential_injector():
    """Credential injector.resolve_arguments() is awaited when set."""
    ex = RPAExecutor()
    ex._playwright_available = False

    injector = AsyncMock()
    injector.resolve_arguments = AsyncMock(return_value={"url": "http://resolved.com"})
    ex._credential_injector = injector

    r = await ex.execute(
        tool_name="rpa_open_url",
        arguments={"url": "vault://creds/my-url"},
    )
    assert r.success is True
    injector.resolve_arguments.assert_awaited_once_with({"url": "vault://creds/my-url"})


@pytest.mark.asyncio
async def test_execute_credential_injector_failure_continues():
    """Credential injector exception is logged; execution continues with original args."""
    ex = RPAExecutor()
    ex._playwright_available = False

    injector = AsyncMock()
    injector.resolve_arguments = AsyncMock(side_effect=RuntimeError("vault unreachable"))
    ex._credential_injector = injector

    r = await ex.execute(
        tool_name="rpa_screenshot",
        arguments={"name": "fallback"},
    )
    # Should still succeed (original args used)
    assert r.success is True


@pytest.mark.asyncio
async def test_execute_with_tenant_and_goal_ids(executor):
    r = await executor.execute(
        tool_name="rpa_click",
        arguments={"selector": "#button"},
        tenant_id="tenant-001",
        goal_id="goal-abc",
    )
    assert r.success is True


# ── _check_playwright ─────────────────────────────────────────────────────────

def test_check_playwright_when_installed():
    """Returns True when playwright can be imported."""
    import sys

    # Temporarily inject a fake playwright module
    import types
    fake_playwright = types.ModuleType("playwright")
    sys.modules["playwright"] = fake_playwright
    try:
        result = RPAExecutor._check_playwright()
    finally:
        sys.modules.pop("playwright", None)
    assert result is True


def test_check_playwright_not_installed():
    """Returns False when playwright is not importable."""
    import sys

    # Remove playwright from sys.modules if present
    original = sys.modules.pop("playwright", None)
    try:
        with patch("builtins.__import__", side_effect=lambda name, *args, **kwargs: (
            (_ for _ in ()).throw(ImportError(f"No module named '{name}'"))
            if name == "playwright"
            else __import__(name, *args, **kwargs)
        )):
            result = RPAExecutor._check_playwright()
    finally:
        if original is not None:
            sys.modules["playwright"] = original
    assert result is False


# ── RPAExecutor constructor ───────────────────────────────────────────────────

def test_constructor_defaults():
    ex = RPAExecutor()
    assert ex._headless is True
    assert ex._artifact_store is None
    assert ex._session_manager is None
    assert ex._vision_provider is None
    assert ex._credential_injector is None


def test_constructor_with_arguments():
    mock_store = MagicMock()
    mock_sm = MagicMock()
    mock_vp = MagicMock()
    ex = RPAExecutor(
        artifact_store=mock_store,
        session_manager=mock_sm,
        headless=False,
        vision_provider=mock_vp,
    )
    assert ex._headless is False
    assert ex._artifact_store is mock_store
    assert ex._session_manager is mock_sm
    assert ex._vision_provider is mock_vp


# ── _execute_playwright_standalone fallback ───────────────────────────────────

@pytest.mark.asyncio
async def test_standalone_falls_back_to_sim_when_import_fails():
    """_execute_playwright_standalone returns sim result when async_playwright fails to import."""
    ex = RPAExecutor()
    ex._playwright_available = True

    import sys

    # Patch sys.modules to make playwright.async_api unavailable
    saved = sys.modules.get("playwright.async_api")
    sys.modules["playwright.async_api"] = None  # type: ignore
    try:
        r = await ex._execute_playwright_standalone(
            tool_name="rpa_open_url",
            arguments={"url": "http://test.com"},
        )
    finally:
        if saved is not None:
            sys.modules["playwright.async_api"] = saved
        else:
            sys.modules.pop("playwright.async_api", None)

    # Falls back to simulation which returns success
    assert isinstance(r, RPAResult)


# ── _execute_with_playwright: full mock of Playwright session ─────────────────
# Mock entire Playwright API to cover lines 108-391 without a real browser.

def _make_playwright_executor():
    """Build an RPAExecutor with mocked Playwright and session manager."""
    ex = RPAExecutor()
    ex._playwright_available = True
    return ex


def _mock_session(url: str = "http://test.com"):
    """Return a mock Playwright session with a fully-mocked page."""
    mock_page = AsyncMock()
    mock_page.title = AsyncMock(return_value="Test Page Title")
    mock_page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    mock_page.inner_text = AsyncMock(return_value="Page content text here")
    mock_page.content = AsyncMock(return_value="<html>Page content</html>")
    mock_page.click = AsyncMock()
    mock_page.fill = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.keyboard = AsyncMock()
    mock_page.keyboard.press = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.select_option = AsyncMock(return_value=["value1"])
    mock_page.set_input_files = AsyncMock()

    # locator mock for form filling
    mock_locator = AsyncMock()
    mock_locator.evaluate = AsyncMock(return_value="input")
    mock_locator.fill = AsyncMock()
    mock_locator.check = AsyncMock()
    mock_locator.uncheck = AsyncMock()
    mock_page.locator = MagicMock(return_value=mock_locator)

    # get_by_text mock
    mock_text_locator = AsyncMock()
    mock_text_locator.first = AsyncMock()
    mock_text_locator.first.click = AsyncMock()
    mock_text_locator.wait_for = AsyncMock()
    mock_page.get_by_text = MagicMock(return_value=mock_text_locator)

    mock_session = MagicMock()
    mock_session.page = mock_page
    mock_session.current_url = url
    mock_session.touch = MagicMock()

    return mock_session, mock_page


@pytest.mark.asyncio
async def test_with_playwright_open_url():
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_open_url",
        arguments={"url": "http://example.com"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is True
    assert "example.com" in r.output or "Test Page Title" in r.output


@pytest.mark.asyncio
async def test_with_playwright_open_url_no_url():
    ex = _make_playwright_executor()
    mock_session, _ = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_open_url",
        arguments={},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is False
    assert "url" in (r.error or "")


@pytest.mark.asyncio
async def test_with_playwright_page_none_falls_back():
    """When session.page is None, falls back to simulation."""
    ex = _make_playwright_executor()
    mock_session = MagicMock()
    mock_session.page = None

    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_screenshot",
        arguments={"name": "fallback"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    # Falls back to simulation (success)
    assert isinstance(r, RPAResult)


@pytest.mark.asyncio
async def test_with_playwright_click_selector():
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_click",
        arguments={"selector": "#submit-btn"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is True
    assert "#submit-btn" in r.output


@pytest.mark.asyncio
async def test_with_playwright_click_text():
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_click",
        arguments={"text": "Click Me Button"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is True


@pytest.mark.asyncio
async def test_with_playwright_click_no_selector_or_text():
    ex = _make_playwright_executor()
    mock_session, _ = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_click",
        arguments={},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is False


@pytest.mark.asyncio
async def test_with_playwright_click_exception():
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_page.click = AsyncMock(side_effect=Exception("Element not found"))
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_click",
        arguments={"selector": "#missing"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is False


@pytest.mark.asyncio
async def test_with_playwright_type():
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_type",
        arguments={"selector": "#username", "text": "testuser"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is True
    assert "#username" in r.output


@pytest.mark.asyncio
async def test_with_playwright_type_no_selector():
    ex = _make_playwright_executor()
    mock_session, _ = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_type",
        arguments={"text": "hello"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is False
    assert "selector" in (r.error or "")


@pytest.mark.asyncio
async def test_with_playwright_extract_text():
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_extract_text",
        arguments={"selector": "#content"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is True
    assert "Page content text here" in r.output


@pytest.mark.asyncio
async def test_with_playwright_screenshot():
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_screenshot",
        arguments={"name": "my_snap"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is True
    assert "my_snap" in r.output
    assert r.artifact_url is not None


@pytest.mark.asyncio
async def test_with_playwright_screenshot_with_artifact_store():
    """Screenshot persists to artifact_store when available."""
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    mock_artifact = MagicMock()
    mock_artifact.uri = "s3://bucket/screenshot.png"
    mock_artifact.name = "screenshot.png"
    mock_store = AsyncMock()
    mock_store.write_bytes = AsyncMock(return_value=mock_artifact)
    ex._artifact_store = mock_store

    r = await ex._execute_with_playwright(
        tool_name="rpa_screenshot",
        arguments={"name": "capture"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is True
    assert r.artifact_url == "s3://bucket/screenshot.png"


@pytest.mark.asyncio
async def test_with_playwright_wait_for_text_found():
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_wait_for_text",
        arguments={"text": "Welcome"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is True
    assert "Welcome" in r.output


@pytest.mark.asyncio
async def test_with_playwright_wait_for_text_in_content():
    """Falls back to content check if wait_for times out but text in HTML."""
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_page.get_by_text = MagicMock()
    mock_locator = AsyncMock()
    mock_locator.wait_for = AsyncMock(side_effect=Exception("timeout"))
    mock_page.get_by_text.return_value = mock_locator
    mock_page.content = AsyncMock(return_value="<html>SearchTarget content</html>")
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_wait_for_text",
        arguments={"text": "SearchTarget"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is True


@pytest.mark.asyncio
async def test_with_playwright_wait_for_text_not_found():
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_locator = AsyncMock()
    mock_locator.wait_for = AsyncMock(side_effect=Exception("timeout"))
    mock_page.get_by_text = MagicMock(return_value=mock_locator)
    mock_page.content = AsyncMock(return_value="<html>No target here</html>")
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_wait_for_text",
        arguments={"text": "MissingText"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is False


@pytest.mark.asyncio
async def test_with_playwright_wait_for_text_no_text():
    ex = _make_playwright_executor()
    mock_session, _ = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_wait_for_text",
        arguments={},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is False


@pytest.mark.asyncio
async def test_with_playwright_select_option():
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_select_option",
        arguments={"selector": "#country", "value": "US"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is True
    assert "US" in r.output


@pytest.mark.asyncio
async def test_with_playwright_select_option_no_selector():
    ex = _make_playwright_executor()
    mock_session, _ = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_select_option",
        arguments={},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is False


@pytest.mark.asyncio
async def test_with_playwright_upload_file(tmp_path):
    """Upload file succeeds when file exists."""
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    test_file = tmp_path / "test_upload.csv"
    test_file.write_text("col1,col2\n1,2\n")

    r = await ex._execute_with_playwright(
        tool_name="rpa_upload_file",
        arguments={"selector": "#file-input", "file_path": str(test_file)},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is True
    assert "test_upload.csv" in r.output


@pytest.mark.asyncio
async def test_with_playwright_upload_file_not_found():
    ex = _make_playwright_executor()
    mock_session, _ = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_upload_file",
        arguments={"selector": "#file", "file_path": "/nonexistent/file.csv"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is False
    assert "File not found" in (r.error or "")


@pytest.mark.asyncio
async def test_with_playwright_upload_file_no_selector():
    ex = _make_playwright_executor()
    mock_session, _ = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_upload_file",
        arguments={"file_path": "/tmp/test.csv"},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is False


@pytest.mark.asyncio
async def test_with_playwright_submit_form():
    ex = _make_playwright_executor()
    mock_session, mock_page = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    # Each field calls evaluate twice: tag name then input type
    mock_locator = AsyncMock()
    mock_locator.evaluate = AsyncMock(side_effect=["input", "text", "input", "text"])
    mock_locator.fill = AsyncMock()
    mock_page.locator = MagicMock(return_value=mock_locator)
    mock_page.click = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()

    r = await ex._execute_with_playwright(
        tool_name="rpa_submit_form",
        arguments={
            "field_values": {"#name": "Alice", "#email": "alice@example.com"},
            "submit_selector": "button[type=submit]",
        },
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert r.success is True
    assert "2" in r.output


@pytest.mark.asyncio
async def test_with_playwright_unknown_tool_falls_back_to_simulation():
    ex = _make_playwright_executor()
    mock_session, _ = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    ex._session_manager = mock_sm

    r = await ex._execute_with_playwright(
        tool_name="rpa_nonexistent_tool",
        arguments={},
        session_id="s-001",
        tenant_id="t-001",
        goal_id="g-001",
    )
    # Falls through to simulation which returns False for unknown tool
    assert r.success is False


@pytest.mark.asyncio
async def test_with_playwright_outer_exception():
    """get_or_create exception propagates (called before the try block)."""
    ex = _make_playwright_executor()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(side_effect=RuntimeError("session failed"))
    ex._session_manager = mock_sm

    with pytest.raises(RuntimeError, match="session failed"):
        await ex._execute_with_playwright(
            tool_name="rpa_open_url",
            arguments={"url": "http://test.com"},
            session_id="s-001",
            tenant_id="t-001",
            goal_id="g-001",
        )


# ── execute() with playwright + session_manager path ────────────────────────

@pytest.mark.asyncio
async def test_execute_with_playwright_and_session_manager():
    """execute() dispatches to _execute_with_playwright when both are available."""
    ex = RPAExecutor()
    ex._playwright_available = True

    mock_session, mock_page = _mock_session()
    mock_sm = AsyncMock()
    mock_sm.get_or_create = AsyncMock(return_value=mock_session)
    mock_sm.close = AsyncMock()
    ex._session_manager = mock_sm

    r = await ex.execute(
        tool_name="rpa_open_url",
        arguments={"url": "http://test.com"},
        session_id="s-persist",
        tenant_id="t-001",
        goal_id="g-001",
    )
    assert isinstance(r, RPAResult)
    assert r.duration_ms >= 0
