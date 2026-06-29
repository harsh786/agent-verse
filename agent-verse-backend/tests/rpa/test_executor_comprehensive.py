"""Comprehensive tests for app/rpa/executor.py — simulation mode only (no real browser)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


async def test_execute_simulation_rpa_open_url() -> None:
    ex = _sim_executor()
    result = await ex.execute(tool_name="rpa_open_url", arguments={"url": "https://example.com"})
    assert result.success is True
    assert "example.com" in result.output


async def test_execute_simulation_rpa_click() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_click",
        arguments={"selector": "#btn", "url": "https://example.com"},
    )
    assert result.success is True
    assert "#btn" in result.output


async def test_execute_simulation_rpa_click_with_text() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_click",
        arguments={"text": "Submit"},
    )
    assert result.success is True
    assert "Submit" in result.output


async def test_execute_simulation_rpa_type() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_type",
        arguments={"selector": "#input", "text": "hello world"},
    )
    assert result.success is True
    assert "hello world" in result.output
    assert "#input" in result.output


async def test_execute_simulation_rpa_extract_text() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_extract_text",
        arguments={"selector": ".content"},
    )
    assert result.success is True
    assert ".content" in result.output


async def test_execute_simulation_rpa_screenshot() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_screenshot",
        arguments={"name": "myshot"},
    )
    assert result.success is True
    assert "myshot" in result.output


async def test_execute_simulation_rpa_wait_for_text() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_wait_for_text",
        arguments={"text": "Loaded"},
    )
    assert result.success is True
    assert "Loaded" in result.output


async def test_execute_simulation_rpa_select_option() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_select_option",
        arguments={"selector": "#dropdown", "value": "option1"},
    )
    assert result.success is True
    assert "option1" in result.output


async def test_execute_simulation_rpa_upload_file() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_upload_file",
        arguments={"selector": "#upload", "file_path": "/tmp/file.txt"},
    )
    assert result.success is True
    assert "file.txt" in result.output


async def test_execute_simulation_rpa_download_file() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_download_file",
        arguments={"selector": "#dl-btn"},
    )
    assert result.success is True
    assert "#dl-btn" in result.output


async def test_execute_simulation_rpa_submit_form() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_submit_form",
        arguments={
            "field_values": {"#name": "Alice", "#email": "alice@example.com"},
            "submit_selector": "#submit-btn",
        },
    )
    assert result.success is True
    # Should mention field count
    assert "2" in result.output


async def test_execute_simulation_rpa_submit_form_empty_fields() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_submit_form",
        arguments={"field_values": {}},
    )
    assert result.success is True


# ── P1.2 Extension tools ──────────────────────────────────────────────────────


async def test_execute_simulation_rpa_detect_captcha() -> None:
    ex = _sim_executor()
    result = await ex.execute(tool_name="rpa_detect_captcha", arguments={})
    assert result.success is True
    assert "captcha" in result.output.lower()


async def test_execute_simulation_rpa_request_human_help() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_request_human_help",
        arguments={"reason": "Need CAPTCHA solved"},
    )
    assert result.success is True
    assert "Need CAPTCHA solved" in result.output


async def test_execute_simulation_rpa_request_human_help_default_reason() -> None:
    ex = _sim_executor()
    result = await ex.execute(tool_name="rpa_request_human_help", arguments={})
    assert result.success is True
    assert "Assistance required" in result.output


async def test_execute_simulation_rpa_wait_for_network_idle() -> None:
    ex = _sim_executor()
    result = await ex.execute(
        tool_name="rpa_wait_for_network_idle",
        arguments={"timeout_ms": 5000},
    )
    assert result.success is True
    assert "5000" in result.output


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
    assert result.success is True


async def test_execute_credential_injector_exception_continues() -> None:
    """If credential injection fails, execution still proceeds (logs warning)."""
    ex = _sim_executor()
    injector = AsyncMock()
    injector.resolve_arguments = AsyncMock(side_effect=RuntimeError("vault unreachable"))
    ex._credential_injector = injector

    result = await ex.execute(
        tool_name="rpa_open_url",
        arguments={"url": "https://example.com"},
    )
    # Execution continues with original args
    assert result.success is True


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


async def test_execute_playwright_standalone_falls_to_simulation_on_import_error() -> None:
    """If playwright import fails in standalone path, falls to simulation."""
    ex = RPAExecutor()
    ex._playwright_available = True
    ex._session_manager = None

    with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
        result = await ex.execute(
            tool_name="rpa_open_url",
            arguments={"url": "https://example.com"},
        )
    # Should fall through to simulation
    assert result.success is True
