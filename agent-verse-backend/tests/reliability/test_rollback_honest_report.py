"""Rollback must actually undo tool calls, under the real tenant, and report honestly.

Regressions:
- The executor registered undo steps with only the tool's INPUT arguments.
  Inverses need the IDs the tool RETURNED (issue id, message ts, ...), so every
  built-in inverse hit its "skipped" branch and did nothing.
- ``rollback_all_async`` still counted every action as rolled back (the
  legacy sync wrapper fire-and-forgot the coroutine and inverses swallowed
  their own errors), so a rollback that undid nothing reported full success.
- Inverses ran under a fabricated ``TenantContext(tenant_id="rollback",
  plan=FREE)`` instead of the goal's tenant.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.governance.hitl import HITLGateway
from app.providers.fake import FakeProvider
from app.reliability.rollback import RollbackEngine
from app.reliability.tool_inverses import InverseResult, register_inverse
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="rb-tenant", plan=PlanTier.ENTERPRISE, api_key_id="rbk1")


class _MCP:
    """Records calls; the forward create returns the new issue id in its OUTPUT."""

    def __init__(self, create_output: Any, *, delete_fails: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._create_output = create_output
        self._delete_fails = delete_fails

    async def call_tool(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        name = kwargs["tool_name"]
        if name == "jira_delete_issue" and self._delete_fails:
            raise RuntimeError("jira 503")

        class _R:
            success = True
            error = ""
            output: Any = None

        r = _R()
        r.output = self._create_output if name == "jira_create_issue" else {"deleted": True}
        return r


def _graph(mcp: _MCP, engine: RollbackEngine) -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=FakeProvider(
            responses=['{"tool": "jira_create_issue", "arguments": {"summary": "Bug"}}']
        ),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        mcp_client=mcp,
        hitl_gateway=HITLGateway(),
        rollback_engine=engine,
    )


def _state() -> AgentState:
    state = AgentState(goal="file a bug", tenant_ctx=T)
    state.steps.append(StepResult(description="create jira issue", status=StepStatus.RUNNING))
    state.context["tool_context"] = ToolContext(
        connectors=[],
        tools=[
            ToolRef(
                server_id="jira-srv",
                server_name="Jira",
                name="jira_create_issue",
                description="create a Jira issue",
                input_schema={},
                auto_approve=True,
            )
        ],
    )
    return state


@pytest.mark.asyncio
async def test_rollback_uses_tool_output_ids_and_real_tenant() -> None:
    mcp = _MCP({"id": "PROJ-7", "key": "PROJ-7"})
    engine = RollbackEngine()
    graph = _graph(mcp, engine)
    await graph._execute_step("create jira issue", _state(), T)
    assert len(engine) == 1

    rolled = await engine.rollback_all_async()

    deletes = [c for c in mcp.calls if c["tool_name"] == "jira_delete_issue"]
    assert len(deletes) == 1, f"inverse did not undo anything: {mcp.calls}"
    assert deletes[0]["arguments"] == {"issue_id": "PROJ-7"}
    assert deletes[0]["server_id"] == "jira-srv"
    assert deletes[0]["tenant_ctx"] is T  # real tenant context, not a fake one
    assert rolled == ["create jira issue"]
    report = engine.last_report
    assert report is not None
    assert report.as_dict()["counts"] == {"rolled_back": 1, "skipped": 0, "failed": 0}


@pytest.mark.asyncio
async def test_rollback_reports_skipped_when_output_has_no_id() -> None:
    mcp = _MCP({"ok": True})  # no id anywhere
    engine = RollbackEngine()
    await _graph(mcp, engine)._execute_step("create jira issue", _state(), T)

    rolled = await engine.rollback_all_async()

    assert rolled == []
    assert not [c for c in mcp.calls if c["tool_name"] == "jira_delete_issue"]
    counts = engine.last_report.as_dict()["counts"]  # type: ignore[union-attr]
    assert counts == {"rolled_back": 0, "skipped": 1, "failed": 0}


@pytest.mark.asyncio
async def test_rollback_reports_failed_when_inverse_errors() -> None:
    mcp = _MCP({"id": "PROJ-8"}, delete_fails=True)
    engine = RollbackEngine()
    await _graph(mcp, engine)._execute_step("create jira issue", _state(), T)

    rolled = await engine.rollback_all_async()

    assert rolled == []
    report = engine.last_report
    assert report is not None
    assert report.as_dict()["counts"] == {"rolled_back": 0, "skipped": 0, "failed": 1}
    assert "jira 503" in report.failed[0]["detail"]


@pytest.mark.asyncio
async def test_stack_mode_classifies_inverse_results() -> None:
    engine = RollbackEngine()

    async def ok() -> None:
        return None

    async def skip() -> InverseResult:
        return InverseResult("skipped", "nothing to undo")

    def boom() -> None:
        raise RuntimeError("nope")

    engine.register(action="a", inverse=ok)
    engine.register(action="b", inverse=skip)
    engine.register(action="c", inverse=boom)
    rolled = await engine.rollback_all_async()
    assert rolled == ["a"]
    assert engine.last_report.as_dict()["counts"] == {  # type: ignore[union-attr]
        "rolled_back": 1,
        "skipped": 1,
        "failed": 1,
    }


@pytest.mark.asyncio
async def test_tool_call_mode_reports_failures_honestly() -> None:
    async def failing_inverse(**_: Any) -> None:
        raise RuntimeError("remote refused")

    register_inverse("test_honest:thing", failing_inverse)

    class _TC:
        tool_name = "test_honest:thing"

    engine = RollbackEngine()
    rolled = await engine.rollback_all_async(executed_tool_calls=[_TC()])
    assert rolled == []
    assert engine.last_report.as_dict()["counts"]["failed"] == 1  # type: ignore[union-attr]
