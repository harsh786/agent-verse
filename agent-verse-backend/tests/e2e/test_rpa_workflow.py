"""CI-safe E2E coverage for a local RPA workflow."""

from __future__ import annotations

from pathlib import Path

from app.agent.tool_calls import extract_tool_call
from app.rpa.artifacts import RPAArtifactStore
from app.rpa.runner import LocalRPARunner, execute_rpa_tool
from app.rpa.session import RPASession


def test_local_rpa_workflow_returns_structured_outputs() -> None:
    session = RPASession(
        session_id="session-e2e",
        tenant_id="tenant-e2e",
        goal_id="goal-rpa-e2e",
    )
    runner = LocalRPARunner(session)
    artifact_store = RPAArtifactStore()

    workflow = [
        ("rpa_open_url", {"url": "https://example.test/search"}),
        ("rpa_type", {"selector": "input[name=q]", "text": "agentverse"}),
        ("rpa_click", {"text": "Search"}),
        ("rpa_extract_text", {}),
        ("rpa_screenshot", {"name": "search-results.png"}),
    ]

    outputs = [
        execute_rpa_tool(tool_name, arguments, session, runner, artifact_store)
        for tool_name, arguments in workflow
    ]

    assert all(output["success"] is True for output in outputs)
    assert [output["current_url"] for output in outputs] == [
        "https://example.test/search",
        "https://example.test/search",
        "https://example.test/search",
        "https://example.test/search",
        "https://example.test/search",
    ]
    assert outputs[3] == {
        "success": True,
        "output": (
            "LocalRPARunner text selector=<page> url=https://example.test/search "
            "values=input[name=q]=agentverse clicks=text:Search"
        ),
        "artifact_uri": None,
        "current_url": "https://example.test/search",
    }
    assert outputs[4] == {
        "success": True,
        "output": "Screenshot captured",
        "artifact_uri": "file:///tmp/agentverse-rpa/goal-rpa-e2e/search-results.png",
        "current_url": "https://example.test/search",
    }
    assert Path("/tmp/agentverse-rpa/goal-rpa-e2e/search-results.png").is_file()


def test_malformed_rpa_screenshot_tool_call_does_not_execute(tmp_path: Path) -> None:
    session = RPASession(
        session_id="session-malformed-rpa",
        tenant_id="tenant-e2e",
        goal_id="goal-malformed-rpa",
    )
    runner = LocalRPARunner(session)
    artifact_store = RPAArtifactStore(base_dir=tmp_path)

    call = extract_tool_call('{"tool": "rpa_screenshot", "arguments": ["bad"]}')
    if call is not None:
        execute_rpa_tool(call.tool, call.arguments, session, runner, artifact_store)

    assert call is None
    assert session.screenshots == []
    assert not (tmp_path / "goal-malformed-rpa" / "screenshot.png").exists()
