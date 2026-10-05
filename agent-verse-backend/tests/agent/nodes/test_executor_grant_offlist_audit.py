"""P8-2: a call to an ungranted tool is refused by the grant gate AND audited.

Ungranted tools are hidden from the models (kept). But a call to one — a
hallucinated or injected tool name — used to be rejected as an "unknown tool"
before the grant gate ran: no ``tool_call_blocked_by_grant`` event and no audit
row (live GOV-GRANT-DENY). Every refusal of the grant gate now writes an audit
row with the tool, the agent, the tenant and the reason.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.governance.audit import AuditEvent, AuditLog
from app.governance.grants.models import Grant
from app.governance.grants.store import InMemoryGrantStore
from app.governance.permissions import ActionLevel
from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-grant-offlist", plan=PlanTier.ENTERPRISE, api_key_id="key-1")
OTHER = TenantContext(tenant_id="t-grant-other", plan=PlanTier.ENTERPRISE, api_key_id="k2")
AGENT = "agent-researcher"


class _MCP:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        class _R:
            success = True
            output = {"results": ["ok"]}
            error = ""

        return _R()


class _ToolCallsProvider(FakeProvider):
    """Structured tool_calls (plural) so the parallel extra-call path runs too."""

    def __init__(self, tool_calls: list[dict[str, Any]]) -> None:
        super().__init__(responses=["ignored"])
        self._tool_calls = tool_calls

    async def stream_tokens(self, request: Any, on_token: Any) -> CompletionResponse:
        self.call_history.append(request)
        await on_token("ok")
        return CompletionResponse(content="ok", model=request.model, input_tokens=5,
                                  output_tokens=1, tool_calls=self._tool_calls)


def _tools() -> ToolContext:
    return ToolContext(connectors=[], tools=[
        ToolRef(server_id=n, server_name="Custom", name=n, description=n, input_schema={})
        for n in ("web_search", "http_request")
    ])


async def _store(scopes: tuple[str, ...] = ("web_search",)) -> InMemoryGrantStore:
    store = InMemoryGrantStore()
    now = datetime.now(UTC)
    await store.issue(Grant(
        grant_id="g-web", tenant_id=T.tenant_id, grantor="owner", grantee_agent_id=AGENT,
        scopes=scopes, not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    ))
    return store


async def _run(executor: FakeProvider, store: InMemoryGrantStore
               ) -> tuple[str, _MCP, list[dict[str, Any]], AuditLog, AgentState]:
    mcp, audit, events = _MCP(), AuditLog(), []
    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]), executor=executor,
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        mcp_client=mcp, grant_store=store, enforce_grants=True, audit_log=audit,
    )
    graph._agent_id = AGENT

    async def _collect(event: dict[str, Any]) -> None:
        events.append(event)

    graph._event_callback = _collect  # type: ignore[assignment]
    state = AgentState(goal="find the stock level", tenant_ctx=T, goal_id="goal-grant-1")
    state.steps.append(StepResult(description="look it up", status=StepStatus.RUNNING))
    state.context["tool_context"] = _tools()
    output = await graph._execute_step("look it up", state, T)
    return output, mcp, events, audit, state


def _denials(audit: AuditLog, ctx: TenantContext = T) -> list[AuditEvent]:
    return [e for e in audit.query(tenant_ctx=ctx, limit=100) if e.outcome == "denied"]


async def test_a_call_to_a_withheld_ungranted_tool_is_denied_and_audited() -> None:
    out, mcp, events, audit, _ = await _run(
        FakeProvider(responses=['{"tool": "http_request", "arguments": {"url": "http://x"}}']),
        await _store(),
    )
    assert mcp.calls == []  # never dispatched
    assert "not granted" in out
    blocked = [e for e in events if e.get("type") == "tool_call_blocked_by_grant"]
    assert [e["tool"] for e in blocked] == ["http_request"]
    (row,) = _denials(audit)
    assert row.tool_name == "http_request"
    assert row.action_level == ActionLevel.DENY
    assert row.goal_id == "goal-grant-1"
    assert f"agent={AGENT}" in row.note and "reason=" in row.note
    assert row.api_key_id == "key-1"
    assert _denials(audit, OTHER) == []  # tenant-scoped


async def test_a_hallucinated_tool_name_goes_through_the_grant_gate() -> None:
    name = "exfiltrate_customer_db_" + "x" * 300
    out, mcp, events, audit, _ = await _run(
        FakeProvider(responses=[f'{{"tool": "{name}", "arguments": {{}}}}']), await _store()
    )
    assert mcp.calls == [] and "not granted" in out
    assert any(e.get("type") == "tool_call_blocked_by_grant" for e in events)
    (row,) = _denials(audit)
    # Over-long name: an explicit prefix + digest, the whole name in the note.
    assert row.tool_name.startswith("exfiltrate_customer_db_") and "sha256:" in row.tool_name
    assert len(row.tool_name) <= 200 and name in row.note


async def test_a_granted_unknown_name_is_an_unknown_tool_not_a_grant_denial() -> None:
    out, mcp, events, audit, _ = await _run(
        FakeProvider(responses=['{"tool": "no_such_tool", "arguments": {}}']),
        await _store(scopes=("*",)),
    )
    assert mcp.calls == []
    assert not any(e.get("type") == "tool_call_blocked_by_grant" for e in events)
    assert _denials(audit) == []


async def test_a_granted_offered_tool_runs_without_a_denial() -> None:
    out, mcp, events, audit, _ = await _run(
        FakeProvider(responses=['{"tool": "web_search", "arguments": {"query": "q"}}']),
        await _store(),
    )
    assert len(mcp.calls) == 1
    assert _denials(audit) == []


async def _parallel(allowed: set[str]) -> tuple[list[tuple[str, str]], list[dict[str, Any]],
                                                 AuditLog, _MCP]:
    mcp, audit, events = _MCP(), AuditLog(), []
    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]), executor=FakeProvider(responses=["x"]),
        verifier=FakeProvider(responses=["{}"]), mcp_client=mcp, grant_store=await _store(),
        enforce_grants=True, audit_log=audit,
    )
    graph._agent_id = AGENT

    async def _collect(event: dict[str, Any]) -> None:
        events.append(event)

    graph._event_callback = _collect  # type: ignore[assignment]
    state = AgentState(goal="g", tenant_ctx=T, goal_id="goal-par")
    state.context["tool_context"] = _tools()
    results = await graph._dispatch_parallel_extra_tool_calls(
        [{"name": "http_request", "input": {"url": "http://x"}}], "look it up", state, T,
        allowed,
    )
    return results, events, audit, mcp


async def test_parallel_extra_call_to_an_ungranted_tool_is_denied_and_audited() -> None:
    # Listed (offered) or withheld from the allowed set: either way the grant
    # gate refuses it and audits the refusal.
    for allowed in ({"web_search", "http_request"}, {"web_search"}):
        results, events, audit, mcp = await _parallel(allowed)
        assert mcp.calls == []
        assert "not granted" in dict(results)["http_request"]
        blocked = [e for e in events if e.get("type") == "tool_call_blocked_by_grant"]
        assert [(e["tool"], e.get("parallel")) for e in blocked] == [("http_request", True)]
        assert [r.tool_name for r in _denials(audit)] == ["http_request"]


async def test_withheld_tools_are_recorded_once_per_goal() -> None:
    events: list[dict[str, Any]] = []
    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]), executor=FakeProvider(responses=["x"]),
        verifier=FakeProvider(responses=["{}"]), grant_store=await _store(),
        enforce_grants=True,
    )
    graph._agent_id = AGENT

    async def _collect(event: dict[str, Any]) -> None:
        events.append(event)

    graph._event_callback = _collect  # type: ignore[assignment]
    state = AgentState(goal="g", tenant_ctx=T, goal_id="goal-w")
    state.context["tool_context"] = _tools()
    assert await graph._granted_tool_names(state, T) == {"web_search"}
    assert await graph._granted_tool_names(state, T) == {"web_search"}  # cached
    withheld = [e for e in events if e.get("type") == "tools_withheld_by_grant"]
    assert [(e["tools"], e["count"]) for e in withheld] == [(["http_request"], 1)]
