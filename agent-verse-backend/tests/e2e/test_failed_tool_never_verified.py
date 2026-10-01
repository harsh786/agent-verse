"""A step whose tool call failed is never a successful step (e2e, API path).

The verifier here ALWAYS answers success. The only tool the agent can call
returns an MCP error (``isError: true``). The goal must not complete: the
failed tool result is a fact the verifier cannot overrule — it can only
downgrade a successful tool result, never upgrade a failed one.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx

from app.agent.graph import AgentGraph
from app.agent.state import GoalStatus
from app.governance.hitl import HITLGateway
from app.providers.fake import FakeProvider
from app.reliability.result_processor import ResultProcessor
from app.tenancy.context import TenantContext
from tests.e2e import test_jira_agent_execution as jira

_ALWAYS_SUCCESS = '{"success": true, "reason": "looks done"}'
_CALL = json.dumps(
    {"tool": "jira_search", "arguments": {"jql": "project = BAU AND statusCategory != Done"}}
)


class _VerifierAlwaysSaysSuccessService(jira.DeterministicJiraGoalService):
    def _make_agent_loop_for_tenant(
        self, tenant_ctx: TenantContext, app_state: Any, **_kwargs: Any
    ) -> AgentGraph:
        return AgentGraph(
            planner=FakeProvider(responses=['{"steps": ["Search Jira for BAU issues"]}']),
            executor=FakeProvider(responses=[_CALL]),
            verifier=FakeProvider(responses=[_ALWAYS_SUCCESS]),
            mcp_client=self._get_mcp_client(),
            hitl_gateway=HITLGateway(),
            result_processor=ResultProcessor(),
        )


def _failing_jira_mcp(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    if payload["method"] == "tools/call":
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "Jira is unavailable (503)"}],
                },
            },
        )
    return jira._mock_jira_mcp(request)


def test_goal_does_not_complete_when_its_tool_failed_and_the_verifier_says_success(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(jira, "DeterministicJiraGoalService", _VerifierAlwaysSaysSuccessService)
    with respx.mock(assert_all_called=False) as router, jira._make_test_client() as client:
        router.post(jira.MOCK_MCP_URL).mock(side_effect=_failing_jira_mcp)
        hdr = {"X-API-Key": jira.TEST_API_KEY}
        connector = client.post(
            "/connectors",
            headers=hdr,
            json={
                "name": "Tickets MCP",
                "url": jira.MOCK_MCP_URL,
                "auth_type": "basic",
                "auth_config": {"username": "jira-user", "password": "jira-token"},
            },
        )
        assert connector.status_code == 201, connector.text
        agent = client.post(
            "/agents",
            headers=hdr,
            json={
                "name": "BAU reader",
                "goal_template": "Search Jira",
                "autonomy_mode": "bounded-autonomous",
                "connector_ids": [connector.json()["server_id"]],
                "trigger_config": {},
            },
        )
        assert agent.status_code == 201, agent.text
        goal = client.post(
            "/goals",
            headers=hdr,
            json={"goal": "Find BAU issues", "agent_id": agent.json()["agent_id"]},
        )
        assert goal.status_code == 202, goal.text
        goal_id = goal.json()["goal_id"]

        final = jira._wait_for_terminal_goal(client, goal_id)
        events = jira._stream_events(client, goal_id)

    tool_events = [e for e in events if e.get("type") == "tool_call_complete"]
    assert tool_events and all(e["success"] is False for e in tool_events)
    verdicts = [e for e in events if e.get("type") == "verification_done"]
    assert verdicts and all(v["success"] is False for v in verdicts)
    assert any("jira_search" in str(v.get("reason", "")) for v in verdicts)
    assert final["status"] == GoalStatus.FAILED.value
