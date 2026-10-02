"""CORE-26: a checkpoint that cannot be written stops further side effects.

``_write_checkpoint`` logged and continued on any DB error, so the durable
record of a completed side-effecting step could be missing while the goal
proceeded; a later crash/redelivery resumed from an older checkpoint and
repeated the step. Now the upsert is retried; if it still fails the goal is
marked ``checkpoint_degraded`` (``checkpoint_write_failed`` event) and every
later non-read tool call fails closed instead of running.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.agent.tool_risk import classify_tool_risk
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="core26-t1", plan=PlanTier.ENTERPRISE, api_key_id="c26")


class _BrokenSession:
    attempts = 0

    def __init__(self) -> None:
        type(self).attempts += 1

    async def __aenter__(self) -> None:
        raise ConnectionError("db connection dropped")

    async def __aexit__(self, *exc: object) -> None:
        return None


class _MCP:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        class Result:
            success = True
            output = {"ok": True}
            error = ""

        return Result()


def _graph(executor: FakeProvider, **kwargs: Any) -> tuple[AgentGraph, list[dict[str, Any]]]:
    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=executor,
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        **kwargs,
    )
    events: list[dict[str, Any]] = []

    async def _cb(evt: dict[str, Any]) -> None:
        events.append(evt)

    graph._event_callback = _cb  # type: ignore[assignment]
    return graph, events


@pytest.mark.asyncio
async def test_failed_checkpoint_write_is_retried_then_flags_the_goal() -> None:
    graph, events = _graph(FakeProvider(responses=["x"]))
    graph._db_session_factory = _BrokenSession
    graph._CHECKPOINT_RETRY_DELAYS_S = (0.0, 0.0)  # type: ignore[attr-defined]
    _BrokenSession.attempts = 0
    state = AgentState(goal="g", tenant_ctx=T)
    state.goal_id = "g" * 32

    await graph._write_checkpoint(state.goal_id, 0, state, T)

    assert _BrokenSession.attempts == 3  # first try + 2 retries
    assert state.context["checkpoint_degraded"] is True
    assert any(e.get("type") == "checkpoint_write_failed" for e in events)


@pytest.mark.asyncio
async def test_side_effecting_tool_is_not_run_once_checkpoints_are_degraded() -> None:
    assert classify_tool_risk("jira_add_comment") == "write_low"
    mcp = _MCP()
    graph, events = _graph(
        FakeProvider(
            responses=['{"tool": "jira_add_comment", "arguments": {"issue_key": "BAU-1"}}']
        ),
        mcp_client=mcp,
        autonomy_mode="fully-autonomous",
    )
    state = AgentState(goal="comment on the issue", tenant_ctx=T)
    state.context["checkpoint_degraded"] = True
    state.steps.append(StepResult(description="comment on BAU-1", status=StepStatus.RUNNING))
    state.context["tool_context"] = ToolContext(
        connectors=[],
        tools=[
            ToolRef(server_id="jira", server_name="Jira", name="jira_add_comment",
                    description="comment", input_schema={})
        ],
    )

    output = await graph._execute_step("comment on BAU-1", state, T)

    assert mcp.calls == []
    assert "checkpoint" in output.lower()
    assert any(e.get("type") == "tool_call_failed" for e in events)
