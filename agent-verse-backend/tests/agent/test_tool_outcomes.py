"""A failed tool result is never upgraded to a successful step by the verifier."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.tool_outcomes import ToolOutcomeLedger, is_failed_tool_result
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="tool-outcomes", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _ok(tool: str) -> dict[str, Any]:
    return {"type": "tool_call_complete", "tool": tool, "success": True}


def _bad(tool: str, error: str = "boom") -> dict[str, Any]:
    return {"type": "tool_call_complete", "tool": tool, "success": False, "error": error}


@pytest.mark.parametrize(
    ("events", "unresolved"),
    [
        ([], []),
        ([_ok("search")], []),
        ([_bad("search", "503")], [("search", "503")]),
        ([{"type": "tool_call_failed", "tool": "send", "error": "MCP down"}], [("send", "MCP down")]),
        ([{"type": "tool_call_blocked_by_policy", "tool": "delete"}],
         [("delete", "tool_call_blocked_by_policy")]),
        ([{"type": "tool_call_blocked_by_grant", "tool": "pay", "reason": "no grant"}],
         [("pay", "no grant")]),
        ([{"type": "tool_call_blocked_by_agent_permission", "tool": "x"}],
         [("x", "tool_call_blocked_by_agent_permission")]),
        # An in-step retry of the same tool that worked resolves the failure...
        ([_bad("search"), _ok("search")], []),
        # ...but a success followed by a failure does not.
        ([_ok("search"), _bad("search", "late")], [("search", "late")]),
        # Another tool succeeding does not resolve a different tool's failure.
        ([_bad("search", "e1"), _ok("summarise")], [("search", "e1")]),
        # Non-result events are ignored.
        ([{"type": "step_started"}, {"type": "tool_call_pending_approval", "tool": "t"}], []),
    ],
)
def test_ledger_unresolved_failures(
    events: list[dict[str, Any]], unresolved: list[tuple[str, str]]
) -> None:
    ledger = ToolOutcomeLedger()
    for event in events:
        ledger.record(event)
    assert ledger.unresolved_failures() == unresolved
    ledger.reset()
    assert ledger.unresolved_failures() == []


@pytest.mark.parametrize(
    ("result", "failed"),
    [
        (SimpleNamespace(success=True), False),
        (SimpleNamespace(success=False), True),
        (SimpleNamespace(), False),
        ({"success": False}, True),
        ({"isError": True, "content": []}, True),
        ({"success": True}, False),
        ({"rows": []}, False),
        ("plain output", False),
    ],
)
def test_is_failed_tool_result(result: Any, failed: bool) -> None:
    assert is_failed_tool_result(result) is failed


@pytest.mark.parametrize(
    ("events", "verdict", "expected"),
    [
        ([_ok("search")], '{"success": true, "reason": "ok"}', True),
        ([_bad("search", "Jira 503")], '{"success": true, "reason": "ok"}', False),
        ([{"type": "tool_call_blocked_by_policy", "tool": "rm"}], '{"success": true}', False),
        ([_bad("search"), _ok("search")], '{"success": true, "reason": "ok"}', True),
        # The verifier can still downgrade a successful tool result.
        ([_ok("search")], '{"success": false, "reason": "wrong data"}', False),
    ],
)
async def test_verifier_cannot_upgrade_a_failed_tool_result(
    events: list[dict[str, Any]], verdict: str, expected: bool
) -> None:
    from app.agent.graph import AgentGraph

    emitted: list[dict[str, Any]] = []

    async def _cb(event: dict[str, Any]) -> None:
        emitted.append(event)

    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=FakeProvider(responses=["done"]),
        verifier=FakeProvider(responses=[verdict]),
    )
    graph._event_callback = _cb  # type: ignore[assignment]
    for event in events:
        await graph._emit(dict(event))
    st = AgentState(goal="find issues", tenant_ctx=T, goal_id="g-tool-outcomes")
    st.plan = ["search"]
    st.steps.append(StepResult(description="search", status=StepStatus.COMPLETE, output="ok"))

    await graph._node_verify({"agent_state": st, "tenant_ctx": T})

    assert st.verification_success is expected
    if not expected and "success\": true" in verdict:
        assert st.verification_feedback.startswith("Tool call(s) failed")
        assert st.context["verification_retry"] is True
    [done] = [e for e in emitted if e.get("type") == "verification_done"]
    assert done["success"] is expected


async def test_a_new_execute_pass_starts_with_a_clean_ledger() -> None:
    """A replan that fixes the failure is not blocked by the previous pass."""
    from app.agent.graph import AgentGraph
    from app.agent.tool_outcomes import ledger_for

    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=FakeProvider(responses=["done"]),
        verifier=FakeProvider(responses=['{"success": true}']),
    )
    await graph._emit(_bad("search"))
    assert ledger_for(graph).unresolved_failures()
    st = AgentState(goal="g", tenant_ctx=T)
    await graph._node_execute({"agent_state": st, "tenant_ctx": T, "plan": []})
    assert ledger_for(graph).unresolved_failures() == []
