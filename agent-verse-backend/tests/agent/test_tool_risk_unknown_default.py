"""Unknown tools must default to an approval-requiring risk class (audit item 1e).

Previously ``classify_tool_risk`` returned ``"read"`` for any unrecognised tool, so an
unknown (possibly side-effecting) MCP tool skipped human approval under supervised and
bounded-autonomous modes. Read-only built-ins must keep their ``read`` classification.
"""

from __future__ import annotations

import pytest

from app.agent.nodes._helpers import resolve_effective_tool_risk
from app.agent.tool_risk import classify_tool_risk
from app.rpa.tools import RPA_TOOLS


def test_unknown_tool_requires_approval() -> None:
    risk = classify_tool_risk("frobnicate_blob", "mystery_connector")
    assert risk == "write_high"
    for mode in ("supervised", "bounded-autonomous"):
        assert (
            resolve_effective_tool_risk(
                risk, autonomy_mode=mode, connector_auto_approve=False, allow_fa_write_high=False
            )
            == "write_high"
        )


def test_empty_tool_name_is_not_an_action() -> None:
    assert classify_tool_risk("", "") == "read"


@pytest.mark.parametrize(
    "name",
    [
        "web_search",
        "parse_document",
        "extract_document",
        "rpa_extract_text",
        "rpa_screenshot",
        "rpa_wait_for_text",
        "rpa_detect_captcha",
    ],
)
def test_read_only_builtins_stay_read(name: str) -> None:
    assert classify_tool_risk(name) == "read"


def test_rpa_tools_are_classified_not_unknown() -> None:
    # RPA tools declare their own risk. Read-only ones stay "read"; interactive ones are
    # "write_low" (executed without approval, exactly as before this change) — they must
    # NOT fall into the unknown → approval bucket and break browser automation.
    expected = {"read": "read", "low": "write_low", "high": "write_low"}
    for tool in RPA_TOOLS:
        assert classify_tool_risk(str(tool["name"]), "rpa") == expected[str(tool["risk"])], tool


class _RecordingMCP:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def call_tool(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        from types import SimpleNamespace

        return SimpleNamespace(success=True, output="ok", error=None)


@pytest.mark.asyncio
async def test_unknown_tool_is_not_dispatched_without_approval_in_bounded_mode() -> None:
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState, StepResult, StepStatus
    from app.agent.tool_context import ToolContext, ToolRef
    from app.providers.fake import FakeProvider
    from app.tenancy.context import PlanTier, TenantContext

    tenant = TenantContext(tenant_id="risk-t", plan=PlanTier.ENTERPRISE, api_key_id="k")
    mcp = _RecordingMCP()
    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=FakeProvider(responses=['{"tool": "frobnicate_blob", "arguments": {}}']),
        verifier=FakeProvider(responses=['{"success": true}']),
        mcp_client=mcp,
        autonomy_mode="bounded-autonomous",
    )
    state = AgentState(goal="goal", tenant_ctx=tenant)
    state.steps.append(StepResult(description="frobnicate the blob", status=StepStatus.RUNNING))
    state.context["tool_context"] = ToolContext(
        connectors=[],
        tools=[
            ToolRef(
                server_id="mystery",
                server_name="mystery_connector",
                name="frobnicate_blob",
                description="does something",
                input_schema={},
            )
        ],
    )

    output = await graph._execute_step("frobnicate the blob", state, tenant)

    assert mcp.calls == []
    assert "requires approval" in output


@pytest.mark.parametrize(
    ("name", "server", "risk"),
    [
        ("get_issue", "github", "read"),
        ("list_repos", "github", "read"),
        ("delete_repo", "github", "destructive"),
        ("create_issue", "github", "write_high"),
        ("update_page", "confluence", "write_low"),
        ("save_artifact", "", "write_low"),
    ],
)
def test_classified_tools_unchanged(name: str, server: str, risk: str) -> None:
    assert classify_tool_risk(name, server) == risk
