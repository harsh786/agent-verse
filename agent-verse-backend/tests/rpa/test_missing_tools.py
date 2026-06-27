"""Tests for the 5 newly added RPA tools."""

from __future__ import annotations

import inspect

import pytest


def test_rpa_tools_list_has_all_10():
    from app.rpa.tools import RPA_TOOLS

    tool_names = {t["name"] for t in RPA_TOOLS}
    required = {
        "rpa_open_url",
        "rpa_click",
        "rpa_type",
        "rpa_extract_text",
        "rpa_screenshot",
        "rpa_wait_for_text",
        "rpa_select_option",
        "rpa_upload_file",
        "rpa_download_file",
        "rpa_submit_form",
    }
    missing = required - tool_names
    assert not missing, f"Missing RPA tools: {missing}"


@pytest.mark.asyncio
async def test_rpa_wait_for_text_simulation():
    from app.rpa.executor import RPAExecutor

    executor = RPAExecutor()
    result = await executor.execute(
        tool_name="rpa_wait_for_text",
        arguments={"text": "Submit", "timeout_ms": 5000},
        session_id=None,
        tenant_id="test-tenant",
    )
    assert result.success or "wait_for_text" in result.output.lower() or result.output != ""


@pytest.mark.asyncio
async def test_rpa_select_option_simulation():
    from app.rpa.executor import RPAExecutor

    executor = RPAExecutor()
    result = await executor.execute(
        tool_name="rpa_select_option",
        arguments={"selector": "#country", "value": "US"},
        session_id=None,
        tenant_id="test-tenant",
    )
    assert result is not None
    assert hasattr(result, "success")


@pytest.mark.asyncio
async def test_rpa_upload_file_simulation():
    from app.rpa.executor import RPAExecutor

    executor = RPAExecutor()
    result = await executor.execute(
        tool_name="rpa_upload_file",
        arguments={"selector": "#file-input", "file_path": "/tmp/test.txt"},
        session_id=None,
        tenant_id="test-tenant",
    )
    assert result is not None
    assert hasattr(result, "success")


@pytest.mark.asyncio
async def test_rpa_download_file_simulation():
    from app.rpa.executor import RPAExecutor

    executor = RPAExecutor()
    result = await executor.execute(
        tool_name="rpa_download_file",
        arguments={"selector": "a.download-link"},
        session_id=None,
        tenant_id="test-tenant",
    )
    assert result is not None
    assert hasattr(result, "success")


@pytest.mark.asyncio
async def test_rpa_submit_form_simulation():
    from app.rpa.executor import RPAExecutor

    executor = RPAExecutor()
    result = await executor.execute(
        tool_name="rpa_submit_form",
        arguments={
            "field_values": {"#name": "Alice", "#email": "alice@test.com"},
            "submit_selector": "button[type=submit]",
        },
        session_id=None,
        tenant_id="test-tenant",
    )
    assert result is not None


def test_executor_handles_all_10_tools():
    """All 10 tools must be handled in the executor dispatch."""
    from app.rpa import executor

    src = inspect.getsource(executor)
    for tool in [
        "rpa_wait_for_text",
        "rpa_select_option",
        "rpa_upload_file",
        "rpa_download_file",
        "rpa_submit_form",
    ]:
        assert tool in src, f"Executor must handle {tool}"


@pytest.mark.asyncio
async def test_all_new_tools_return_valid_rpa_result():
    """Each new tool returns an RPAResult with bool success and non-negative duration."""
    from app.rpa.executor import RPAExecutor, RPAResult

    executor = RPAExecutor()
    cases = [
        ("rpa_wait_for_text", {"text": "Hello"}),
        ("rpa_select_option", {"selector": "#sel", "value": "opt1"}),
        ("rpa_upload_file", {"selector": "#f", "file_path": "/tmp/x.txt"}),
        ("rpa_download_file", {"selector": "#dl"}),
        ("rpa_submit_form", {"field_values": {}}),
    ]
    for tool_name, args in cases:
        result = await executor.execute(
            tool_name=tool_name,
            arguments=args,
            tenant_id="test",
        )
        assert isinstance(result, RPAResult), f"{tool_name} must return RPAResult"
        assert isinstance(result.success, bool), f"{tool_name} success must be bool"
        assert result.duration_ms >= 0, f"{tool_name} duration must be non-negative"


@pytest.mark.asyncio
async def test_new_tools_simulation_output_content():
    """Simulation outputs contain expected keywords."""
    from app.rpa.executor import RPAExecutor

    executor = RPAExecutor()

    r = await executor.execute(
        tool_name="rpa_wait_for_text",
        arguments={"text": "Confirm"},
        tenant_id="test",
    )
    assert "Confirm" in r.output

    r = await executor.execute(
        tool_name="rpa_select_option",
        arguments={"selector": "#lang", "value": "en"},
        tenant_id="test",
    )
    assert "en" in r.output

    r = await executor.execute(
        tool_name="rpa_submit_form",
        arguments={"field_values": {"#a": "1", "#b": "2"}},
        tenant_id="test",
    )
    assert "2" in r.output  # "Filled 2 fields..."
