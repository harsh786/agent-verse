"""Tests for the RPA tool surface and local runner adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.rpa.artifacts import RPAArtifactStore
from app.rpa.runner import LocalRPARunner, execute_rpa_tool
from app.rpa.session import RPASession
from app.rpa.tools import RPA_TOOLS, classify_rpa_tool_risk


def test_rpa_tool_definitions_include_expected_tools() -> None:
    tools_by_name = {tool["name"]: tool for tool in RPA_TOOLS}

    expected_original = {
        "rpa_open_url",
        "rpa_click",
        "rpa_type",
        "rpa_extract_text",
        "rpa_screenshot",
    }
    assert expected_original.issubset(set(tools_by_name)), (
        f"Original 5 tools must still be present; missing: {expected_original - set(tools_by_name)}"
    )
    assert tools_by_name["rpa_open_url"]["risk"] == "low"
    assert tools_by_name["rpa_click"]["risk"] == "high"
    assert tools_by_name["rpa_type"]["risk"] == "high"
    assert tools_by_name["rpa_extract_text"]["risk"] == "read"
    assert tools_by_name["rpa_screenshot"]["risk"] == "read"
    assert classify_rpa_tool_risk("rpa_click") == "high"
    assert classify_rpa_tool_risk("missing") == "unknown"


def test_local_rpa_runner_sequence_writes_screenshot_artifact() -> None:
    session = RPASession(
        session_id="session-1",
        tenant_id="tenant-1",
        goal_id="goal-rpa-tools",
    )
    runner = LocalRPARunner(session)
    artifact_store = RPAArtifactStore()

    open_result = execute_rpa_tool(
        "rpa_open_url",
        {"url": "https://example.test/login"},
        session,
        runner,
        artifact_store,
    )
    type_result = execute_rpa_tool(
        "rpa_type",
        {"selector": "#email", "text": "alice@example.test"},
        session,
        runner,
        artifact_store,
    )
    click_result = execute_rpa_tool(
        "rpa_click",
        {"selector": "#submit"},
        session,
        runner,
        artifact_store,
    )
    extract_result = execute_rpa_tool(
        "rpa_extract_text",
        {"selector": "main"},
        session,
        runner,
        artifact_store,
    )
    screenshot_result = execute_rpa_tool(
        "rpa_screenshot",
        {"name": "login.png"},
        session,
        runner,
        artifact_store,
    )

    assert open_result == {
        "success": True,
        "output": "Opened https://example.test/login",
        "artifact_uri": None,
        "current_url": "https://example.test/login",
    }
    assert type_result["success"] is True
    assert click_result["success"] is True
    assert extract_result == {
        "success": True,
        "output": (
            "LocalRPARunner text selector=main url=https://example.test/login "
            "values=#email=alice@example.test clicks=#submit"
        ),
        "artifact_uri": None,
        "current_url": "https://example.test/login",
    }
    assert screenshot_result["success"] is True
    assert screenshot_result["artifact_uri"] == "file:///tmp/agentverse-rpa/goal-rpa-tools/login.png"
    assert Path("/tmp/agentverse-rpa/goal-rpa-tools/login.png").read_bytes() == (
        b"agentverse-local-rpa-screenshot\n"
        b"session=session-1\n"
        b"url=https://example.test/login\n"
    )
    assert session.current_url == "https://example.test/login"
    assert session.screenshots == ["file:///tmp/agentverse-rpa/goal-rpa-tools/login.png"]


def test_artifact_store_sanitizes_goal_id_under_base_dir(tmp_path: Path) -> None:
    base_dir = tmp_path / "artifacts"
    store = RPAArtifactStore(base_dir=base_dir)

    artifact = store.write_bytes(
        goal_id="../../escape",
        name="../screenshot.png",
        content=b"safe",
    )

    artifact_path = Path(artifact.path).resolve()
    assert artifact_path.is_relative_to(base_dir.resolve())
    assert artifact_path.read_bytes() == b"safe"
    assert not (base_dir / "../../escape/screenshot.png").resolve().is_file()


def test_execute_rpa_tool_returns_failure_for_missing_open_url_argument() -> None:
    session = RPASession(session_id="session-2", tenant_id="tenant-1", goal_id="goal-open")
    session.current_url = "https://current.test"

    result = execute_rpa_tool(
        "rpa_open_url",
        {},
        session,
        LocalRPARunner(session),
        RPAArtifactStore(),
    )

    assert result == {
        "success": False,
        "error": "Missing required RPA argument: url",
        "artifact_uri": None,
        "current_url": "https://current.test",
    }


def test_execute_rpa_tool_returns_failure_for_missing_click_target() -> None:
    session = RPASession(session_id="session-click", tenant_id="tenant-1", goal_id="goal-click")
    runner = LocalRPARunner(session)

    result = execute_rpa_tool(
        "rpa_click",
        {},
        session,
        runner,
        RPAArtifactStore(),
    )

    assert result == {
        "success": False,
        "error": "Missing required RPA argument: selector or text",
        "artifact_uri": None,
        "current_url": None,
    }
    assert runner.clicked_targets == []


def test_execute_rpa_tool_clicks_by_text() -> None:
    session = RPASession(
        session_id="session-click-text",
        tenant_id="tenant-1",
        goal_id="goal-click",
    )
    runner = LocalRPARunner(session)

    result = execute_rpa_tool(
        "rpa_click",
        {"text": "Submit"},
        session,
        runner,
        RPAArtifactStore(),
    )

    assert result == {
        "success": True,
        "output": "Clicked text:Submit",
        "artifact_uri": None,
        "current_url": None,
    }
    assert runner.clicked_targets == ["text:Submit"]


@pytest.mark.parametrize(
    ("arguments", "missing_argument"),
    [
        ({"text": "alice@example.test"}, "selector"),
        ({"selector": "#email"}, "text"),
    ],
)
def test_execute_rpa_tool_returns_failure_for_missing_type_arguments(
    arguments: dict[str, str],
    missing_argument: str,
) -> None:
    session = RPASession(session_id="session-3", tenant_id="tenant-1", goal_id="goal-type")

    result = execute_rpa_tool(
        "rpa_type",
        arguments,
        session,
        LocalRPARunner(session),
        RPAArtifactStore(),
    )

    assert result == {
        "success": False,
        "error": f"Missing required RPA argument: {missing_argument}",
        "artifact_uri": None,
        "current_url": None,
    }


def test_execute_rpa_tool_returns_failure_for_unknown_tool() -> None:
    session = RPASession(session_id="session-2", tenant_id="tenant-1", goal_id="goal-unknown")

    result = execute_rpa_tool(
        "rpa_missing",
        {},
        session,
        LocalRPARunner(session),
        RPAArtifactStore(),
    )

    assert result == {
        "success": False,
        "error": "Unknown RPA tool: rpa_missing",
        "artifact_uri": None,
        "current_url": None,
    }


# ── New tests: executor, session store, and metadata ──────────────────────────


def test_rpa_tools_metadata() -> None:
    """All RPA tools have the required metadata fields."""
    assert len(RPA_TOOLS) == 10
    for tool in RPA_TOOLS:
        assert "name" in tool, f"Tool missing 'name': {tool}"
        assert "description" in tool, f"Tool missing 'description': {tool}"
        assert "risk" in tool, f"Tool missing 'risk': {tool}"
        assert "input_schema" in tool, f"Tool missing 'input_schema': {tool}"


def test_classify_rpa_tool_risk_all_tools() -> None:
    """classify_rpa_tool_risk returns the correct risk level for every built-in tool."""
    assert classify_rpa_tool_risk("rpa_open_url") == "low"
    assert classify_rpa_tool_risk("rpa_click") == "high"
    assert classify_rpa_tool_risk("rpa_type") == "high"
    assert classify_rpa_tool_risk("rpa_extract_text") == "read"
    assert classify_rpa_tool_risk("rpa_screenshot") == "read"
    assert classify_rpa_tool_risk("rpa_unknown") == "unknown"


@pytest.mark.asyncio
async def test_rpa_executor_simulation() -> None:
    """RPAExecutor falls back to simulation when Playwright is not available."""
    from app.rpa.executor import RPAExecutor

    executor = RPAExecutor()
    result = await executor.execute(
        tool_name="rpa_open_url",
        arguments={"url": "https://example.com"},
        tenant_id="test",
    )
    assert result.success is True
    assert "example.com" in result.output or "simulated" in result.output
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_rpa_executor_all_tools() -> None:
    """All RPA tools can be executed by RPAExecutor without crashing."""
    from app.rpa.executor import RPAExecutor

    executor = RPAExecutor()
    for tool in RPA_TOOLS:
        result = await executor.execute(
            tool_name=tool["name"],
            arguments={},
            tenant_id="test",
        )
        assert isinstance(result.success, bool), (
            f"Tool {tool['name']} returned non-bool success"
        )
        assert result.duration_ms >= 0, (
            f"Tool {tool['name']} returned negative duration"
        )


@pytest.mark.asyncio
async def test_rpa_session_store() -> None:
    """RPASessionStore CRUD works correctly with tenant isolation."""
    from app.rpa.session import RPASessionStore

    store = RPASessionStore()

    # Create
    session = await store.create(tenant_id="t1")
    assert session.session_id
    assert session.status == "active"

    # Get by tenant
    fetched = await store.get(session.session_id, tenant_id="t1")
    assert fetched is not None
    assert fetched.session_id == session.session_id

    # Tenant isolation: another tenant cannot see this session
    other = await store.get(session.session_id, tenant_id="t2")
    assert other is None

    # List active
    active_before = await store.list_active(tenant_id="t1")
    assert any(s.session_id == session.session_id for s in active_before)

    # Close
    closed = await store.close(session.session_id, tenant_id="t1")
    assert closed is True

    # No longer in active list
    active_after = await store.list_active(tenant_id="t1")
    assert not any(s.session_id == session.session_id for s in active_after)


@pytest.mark.asyncio
async def test_rpa_session_store_close_wrong_tenant_returns_false() -> None:
    """Closing a session with the wrong tenant ID fails gracefully."""
    from app.rpa.session import RPASessionStore

    store = RPASessionStore()
    session = await store.create(tenant_id="owner")
    result = await store.close(session.session_id, tenant_id="other-tenant")
    assert result is False
    # Original session is still active
    assert await store.get(session.session_id, tenant_id="owner") is not None
