"""Comprehensive tests for app/rpa/executor.py — simulation mode only (no real browser)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.rpa.executor import RPAExecutor, RPAResult

# ── RPAResult dataclass ───────────────────────────────────────────────────────


def test_rpa_result_defaults() -> None:
    r = RPAResult(success=True)
    assert r.output == ""
    assert r.artifact_url is None
    assert r.artifact_name is None
    assert r.duration_ms == 0.0
    assert r.error is None


def test_rpa_result_failure() -> None:
    r = RPAResult(success=False, error="something broke")
    assert r.success is False
    assert r.error == "something broke"


def test_rpa_result_with_artifact() -> None:
    r = RPAResult(
        success=True,
        output="captured",
        artifact_url="data:image/png;base64,abc123",
        artifact_name="screenshot.png",
    )
    assert r.artifact_url is not None
    assert r.artifact_name == "screenshot.png"


# ── RPAExecutor construction and playwright check ─────────────────────────────


def test_executor_construction() -> None:
    ex = RPAExecutor()
    assert ex._headless is True
    assert ex._artifact_store is None
    assert ex._session_manager is None


def test_executor_check_playwright_returns_bool() -> None:
    result = RPAExecutor._check_playwright()
    assert isinstance(result, bool)


def test_executor_playwright_available_false_when_not_installed() -> None:
    with patch.dict("sys.modules", {"playwright": None}):
        result = RPAExecutor._check_playwright()
        assert result is False


# ── Simulation mode — all built-in tools ─────────────────────────────────────


def _sim_executor() -> RPAExecutor:
    """Return an executor that always goes to simulation (no playwright, no session_manager)."""
    ex = RPAExecutor()
    ex._playwright_available = False
    return ex


_ALL_TOOLS = [
    ("rpa_open_url", {"url": "https://example.com"}),
    ("rpa_click", {"selector": "#submit"}),
    ("rpa_click", {"text": "Log in"}),
    ("rpa_type", {"selector": "#q", "text": "hello"}),
    ("rpa_extract_text", {"selector": "h1"}),
    ("rpa_screenshot", {"name": "page"}),
    ("rpa_wait_for_text", {"text": "Welcome"}),
    ("rpa_select_option", {"selector": "#c", "value": "US"}),
    ("rpa_upload_file", {"selector": "#f", "file_path": "/tmp/x.pdf"}),
    ("rpa_download_file", {"selector": "#dl"}),
    ("rpa_submit_form", {"field_values": {"#a": "1"}}),
    ("rpa_submit_form", {}),
    ("rpa_detect_captcha", {}),
    ("rpa_request_human_help", {"reason": "blocked"}),
    ("rpa_wait_for_network_idle", {"timeout_ms": 3000}),
]


@pytest.mark.parametrize(("tool", "args"), _ALL_TOOLS)
async def test_execute_without_browser_is_not_implemented(
    tool: str, args: dict[str, object]
) -> None:
    """No browser → an explicit NOT IMPLEMENTED failure, never fake success."""
    ex = _sim_executor()
    result = await ex.execute(tool_name=tool, arguments=args)
    assert result.success is False
    assert "NOT IMPLEMENTED" in (result.error or "")
    assert "[simulated]" not in result.output


async def test_execute_simulation_unknown_tool() -> None:
    ex = _sim_executor()
    result = await ex.execute(tool_name="rpa_unknown_tool", arguments={})
    assert result.success is False
    assert "Unknown RPA tool" in (result.error or "")


# ── Duration is always set ────────────────────────────────────────────────────


async def test_execute_sets_duration_ms() -> None:
    ex = _sim_executor()
    result = await ex.execute(tool_name="rpa_open_url", arguments={"url": "https://x.com"})
    assert result.duration_ms > 0.0


# ── Credential injector path ──────────────────────────────────────────────────


async def test_execute_credential_injector_resolves_args() -> None:
    ex = _sim_executor()
    injector = AsyncMock()
    injector.resolve_arguments = AsyncMock(
        return_value={"url": "https://resolved.com"}
    )
    ex._credential_injector = injector

    result = await ex.execute(
        tool_name="rpa_open_url",
        arguments={"url": "vault://my-cred"},
    )
    injector.resolve_arguments.assert_called_once()
    assert result.success is False
    assert "NOT IMPLEMENTED" in (result.error or "")


async def test_execute_credential_injector_exception_fails_closed() -> None:
    """If credential injection fails the command is NOT run with raw vault refs."""
    ex = _sim_executor()
    injector = AsyncMock()
    injector.resolve_arguments = AsyncMock(side_effect=RuntimeError("vault unreachable"))
    ex._credential_injector = injector

    result = await ex.execute(
        tool_name="rpa_open_url",
        arguments={"url": "https://example.com"},
    )
    assert result.success is False
    assert "credential injection failed" in (result.error or "")


# ── Ephemeral session cleanup ─────────────────────────────────────────────────


async def test_execute_ephemeral_session_closed_after_call() -> None:
    """When no session_id is given, executor should close the ephemeral session."""
    mock_session_mgr = AsyncMock()
    mock_session_mgr.close = AsyncMock()

    ex = RPAExecutor(session_manager=mock_session_mgr)
    ex._playwright_available = False  # Force simulation

    await ex.execute(tool_name="rpa_open_url", arguments={"url": "https://x.com"})
    # session_manager.close should have been called with ephemeral session id
    mock_session_mgr.close.assert_called_once()


async def test_execute_non_ephemeral_session_not_auto_closed() -> None:
    """When session_id is provided, session is NOT auto-closed."""
    mock_session_mgr = AsyncMock()
    mock_session_mgr.close = AsyncMock()

    ex = RPAExecutor(session_manager=mock_session_mgr)
    ex._playwright_available = False

    await ex.execute(
        tool_name="rpa_open_url",
        arguments={"url": "https://x.com"},
        session_id="my-persistent-session",
    )
    mock_session_mgr.close.assert_not_called()


# ── Playwright available but no session manager → standalone ─────────────────


async def test_execute_playwright_standalone_import_error_is_not_implemented() -> None:
    """If playwright import fails in standalone path, report it — no fake success."""
    ex = RPAExecutor()
    ex._playwright_available = True
    ex._session_manager = None

    with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
        result = await ex.execute(
            tool_name="rpa_open_url",
            arguments={"url": "https://example.com"},
        )
    assert result.success is False
    assert "NOT IMPLEMENTED" in (result.error or "")
