"""Regression: under grant enforcement the planner was offered every tool.

Real run (on-prem Qwen + NVIDIA kimi): the answer was in the retrieved knowledge,
but the planner saw the RPA/browser tools, planned rpa_open_url/rpa_click/...,
every call was denied by the grant gate, and the goal failed after 3 replans.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.agent.graph import AgentGraph
from app.agent.nodes.planner_mixin import GRANTED_TOOLS_KEY
from app.agent.state import AgentState
from app.agent.tool_context import ToolContext, ToolRef
from app.governance.grants import Grant, InMemoryGrantStore
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")


def _tool(name: str) -> ToolRef:
    return ToolRef("s", "Srv", name, f"{name} tool", {"type": "object", "properties": {}})


def _graph(store: InMemoryGrantStore | None) -> tuple[AgentGraph, FakeProvider]:
    planner = FakeProvider(responses=['{"steps": ["answer from the knowledge base"]}'])
    other = FakeProvider(responses=["x"])
    g = AgentGraph(planner=planner, executor=other, verifier=other, grant_store=store,
                   enforce_grants=True)
    g._agent_id = "agent-1"
    return g, planner


def _state() -> AgentState:
    st = AgentState(goal="what is the refund window", tenant_ctx=CTX)
    tools = [_tool("jira_search"), _tool("rpa_open_url"), _tool("rpa_click")]
    st.context["tool_context"] = ToolContext(connectors=[], tools=tools)
    st.context["tool_prompt"] = "Available tools:\n- Srv.jira_search\n- Srv.rpa_open_url"
    return st


async def test_planner_prompt_lists_only_granted_tools() -> None:
    store = InMemoryGrantStore()
    now = datetime.now(UTC)
    await store.issue(Grant(grant_id="g1", tenant_id="t1", grantor="u", grantee_agent_id="agent-1",
                            scopes=("jira_*",), not_before=now - timedelta(hours=1),
                            expires_at=now + timedelta(hours=1)))
    g, planner = _graph(store)
    st = _state()
    await g._node_plan({"agent_state": st, "tenant_ctx": CTX, "goal": st.goal})
    prompt = " ".join(str(m.content) for m in planner.call_history[0].messages)
    assert "jira_search" in prompt
    assert "rpa_open_url" not in prompt and "rpa_click" not in prompt
    assert st.context[GRANTED_TOOLS_KEY] == ["jira_search"]


async def test_no_grants_means_no_tools_offered() -> None:
    g, planner = _graph(InMemoryGrantStore())
    st = _state()
    await g._node_plan({"agent_state": st, "tenant_ctx": CTX, "goal": st.goal})
    prompt = " ".join(str(m.content) for m in planner.call_history[0].messages)
    assert "rpa_open_url" not in prompt and "jira_search" not in prompt
    assert "No tools are granted" in prompt


async def test_without_enforcement_the_catalogue_is_unchanged() -> None:
    g, planner = _graph(None)
    g._enforce_grants = False
    st = _state()
    await g._node_plan({"agent_state": st, "tenant_ctx": CTX, "goal": st.goal})
    prompt = " ".join(str(m.content) for m in planner.call_history[0].messages)
    assert "rpa_open_url" in prompt
