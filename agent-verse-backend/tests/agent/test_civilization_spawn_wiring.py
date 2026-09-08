"""Agent-level tests: the civilization_spawn tool is advertised to the LLM and
its executor dispatch branch actually fires end-to-end.

Regression for a dead-end wiring bug: ``SPAWN_TOOL_DEFINITION`` was defined in
``app/civilization/spawn_tool.py`` but never added to the executor's
LLM-advertised tool list (``_tool_defs``). Because the tool was never offered,
the LLM never emitted a ``civilization_spawn`` call and the spawn dispatch branch
was unreachable.
"""

from __future__ import annotations

from typing import Any

import pytest

import app.civilization.spawn_tool as spawn_tool_mod
from app.agent.graph import AgentGraph
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(
    tenant_id="civ-wire-tenant", plan=PlanTier.ENTERPRISE, api_key_id="civ-wire-key"
)

CIV_ID = "civ-wire-123"


def _civ_provider() -> FakeProvider:
    """Planner emits one spawn step, executor emits a civilization_spawn call,
    verifier reports success."""
    return FakeProvider(
        responses=[
            '{"steps": ["spawn a helper agent to triage the backlog"]}',
            (
                '{"tool": "civilization_spawn", "arguments": '
                '{"capability": "triage", "goal": "triage the backlog", '
                '"priority": "high"}}'
            ),
            '{"success": true, "reason": "done"}',
        ]
    )


async def test_civilization_spawn_tool_offered_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A civilization-enabled agent advertises civilization_spawn in the
    executor's LLM tool list."""

    async def _recorder(**kwargs: Any) -> dict[str, Any]:
        return {"success": True, "agent_id": "child-1", "goal_id": "g-1"}

    monkeypatch.setattr(spawn_tool_mod, "execute_spawn_tool", _recorder)

    p = _civ_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    # Production civ runs are wired with an MCP client (goal_service). The spawn
    # dispatch branch lives under the "mcp_client present" path, so give the graph
    # a truthy client to reflect that wiring.
    g._mcp_client = object()
    await g.run(
        goal="coordinate agents",
        tenant_ctx=T,
        initial_context={"civilization_id": CIV_ID},
    )

    offered = {
        td.name for req in p.call_history for td in (req.tools or [])
    }
    assert "civilization_spawn" in offered


async def test_civilization_spawn_branch_executes_with_governance_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spawn dispatch branch fires and calls execute_spawn_tool with the
    unpacked tool arguments and injected governance context."""
    calls: list[dict[str, Any]] = []

    async def _recorder(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"success": True, "agent_id": "child-1", "goal_id": "g-1", "depth": 1}

    monkeypatch.setattr(spawn_tool_mod, "execute_spawn_tool", _recorder)

    events: list[dict[str, Any]] = []

    async def _cb(event: dict[str, Any]) -> None:
        events.append(event)

    p = _civ_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    g._mcp_client = object()
    await g.run(
        goal="coordinate agents",
        tenant_ctx=T,
        initial_context={"civilization_id": CIV_ID},
        event_callback=_cb,
    )

    assert len(calls) == 1, f"expected exactly one spawn call, got {len(calls)}"
    kw = calls[0]
    # Arguments unpacked from the tool call
    assert kw["capability"] == "triage"
    assert kw["goal"] == "triage the backlog"
    assert kw["priority"] == "high"
    # Civilization identity injected by the executor
    assert kw["civilization_id"] == CIV_ID
    # Governance context plumbed through (safe defaults are acceptable)
    assert "depth" in kw
    assert "parent_budget_usd" in kw
    assert "parent_policy_ids" in kw

    spawned = [e for e in events if e.get("type") == "child_agent_spawned"]
    assert spawned, "expected a child_agent_spawned event"
    assert spawned[0].get("capability") == "triage"

    # The spawn result must not be clobbered by the trailing "tool not found"
    # fallthrough: no spurious tool_call_failed for civilization_spawn.
    failed = [
        e
        for e in events
        if e.get("type") == "tool_call_failed" and e.get("tool") == "civilization_spawn"
    ]
    assert not failed, f"unexpected tool_call_failed for civilization_spawn: {failed}"
