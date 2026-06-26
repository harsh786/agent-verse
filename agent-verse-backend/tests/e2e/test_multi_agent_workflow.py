"""End-to-end coverage for minimal multi-agent workflow execution."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.agent.state import GoalStatus
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(
    tenant_id="workflow-e2e-tenant",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="workflow-e2e-key",
)


class _FakeAgentStore:
    def __init__(self, agent: dict[str, Any]) -> None:
        self._agent = agent

    def get(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
        if agent_id == self._agent["id"] and tenant_ctx == _CTX:
            return self._agent
        return None


class _FakeWorkflowMCPClient:
    def __init__(self, *, output: Any) -> None:
        self._output = output

    async def discover_tools(
        self, *, server_id: str, tenant_ctx: TenantContext
    ) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                server_id=server_id,
                server_name="Jira MCP",
                name="jira_search",
                description="Search Jira issues",
                input_schema={"type": "object"},
            )
        ]

    async def call_tool(
        self,
        *,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_ctx: TenantContext,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            tool_name=tool_name,
            success=True,
            output=self._output,
            error="",
            server_id=server_id,
        )


async def test_complex_multi_agent_goal_emits_events_in_dependency_order() -> None:
    svc = GoalService()

    result = await svc.submit_goal(
        goal="For Jira issues, create a Confluence page and send an email update",
        priority="normal",
        dry_run=False,
        tenant_ctx=_CTX,
        workflow_mode="multi_agent",
    )
    record = svc._goals[result["goal_id"]]
    assert record.task is not None
    await record.task

    goal = await svc.get_goal(result["goal_id"], tenant_ctx=_CTX)
    events = await svc.get_events(result["goal_id"], tenant_ctx=_CTX)
    completed_step_ids = [
        event["step_id"]
        for event in events
        if event["type"] == "workflow_step_complete"
    ]

    assert goal["status"] == GoalStatus.COMPLETE.value
    assert completed_step_ids == ["step_1", "step_2", "step_3"]
    assert events[1]["type"] == "workflow_planned"
    assert [step["input_from"] for step in events[1]["steps"]] == [
        [],
        ["step_1"],
        ["step_1", "step_2"],
    ]


async def test_workflow_step_complete_redacts_authorization_bearer_tool_output() -> None:
    agent = {"id": "agent-123", "name": "Jira Agent", "connector_ids": ["jira-server"]}
    mcp_client = _FakeWorkflowMCPClient(
        output={"headers": "Authorization: Bearer workflow-secret"}
    )
    svc = GoalService(
        app_state=SimpleNamespace(
            agent_store=_FakeAgentStore(agent),
            mcp_client=mcp_client,
        )
    )

    result = await svc.submit_goal(
        goal="Fetch open Jira issues",
        priority="normal",
        dry_run=False,
        tenant_ctx=_CTX,
        agent_id="agent-123",
        workflow_mode="multi_agent",
    )
    record = svc._goals[result["goal_id"]]
    assert record.task is not None
    await record.task

    events = await svc.get_events(result["goal_id"], tenant_ctx=_CTX)
    complete_event = next(
        event for event in events if event["type"] == "workflow_step_complete"
    )

    assert "workflow-secret" not in str(complete_event)
    assert complete_event["output"]["output"]["headers"] == (
        "Authorization: Bearer [REDACTED]"
    )
