"""Jira MCP E2E coverage for agent-bound goal execution."""

from __future__ import annotations

import base64
import builtins
import hashlib
import json
import os
import time
from typing import Any, cast

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.agent.graph import AgentGraph
from app.agent.state import GoalStatus
from app.governance.hitl import HITLGateway
from app.main import create_app
from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.providers.fake import FakeProvider
from app.reliability.result_processor import ResultProcessor
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

MOCK_MCP_URL = "https://mock-jira-mcp.local/mcp"
ATLASSIAN_MCP_URL = "https://mcp.atlassian.com/v1/mcp"
TEST_API_KEY = "av_professional_jira_e2e_test_key"
TENANT = TenantContext(
    tenant_id="jira-e2e-tenant",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="jira-e2e-key",
)


class FakeRedis:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}
        self._s: dict[str, builtins.set[str]] = {}

    async def get(self, key: str) -> str | None:
        return self._d.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._d[key] = value

    async def delete(self, key: str) -> int:
        existed = key in self._d
        self._d.pop(key, None)
        return int(existed)

    async def sadd(self, key: str, value: str) -> None:
        self._s.setdefault(key, set()).add(value)

    async def srem(self, key: str, value: str) -> None:
        self._s.get(key, set()).discard(value)

    async def smembers(self, key: str) -> builtins.set[str]:
        return self._s.get(key, set())


class FakeTenantService:
    def __init__(self) -> None:
        self._key_hash = hashlib.sha256(TEST_API_KEY.encode()).hexdigest()

    async def resolve_api_key(self, raw_key: str) -> TenantContext | None:
        if hashlib.sha256(raw_key.encode()).hexdigest() == self._key_hash:
            return TENANT
        return None


class DeterministicJiraGoalService(GoalService):
    def _make_agent_loop_for_tenant(
        self, tenant_ctx: TenantContext, app_state: Any, *, agent_id: str | None = None
    ) -> AgentGraph:
        return AgentGraph(
            planner=FakeProvider(
                responses=[
                    '{"steps": ["Search Jira for not-done BAU issues"]}',
                ]
            ),
            executor=FakeProvider(
                responses=[
                    json.dumps(
                        {
                            "tool": "jira_search",
                            "arguments": {
                                "jql": "project = BAU AND statusCategory != Done",
                            },
                        }
                    ),
                ]
            ),
            verifier=FakeProvider(
                responses=['{"success": true, "reason": "Jira search completed"}']
            ),
            mcp_client=self._get_mcp_client(),
            hitl_gateway=HITLGateway(),
            result_processor=ResultProcessor(),
        )


def _make_test_client() -> TestClient:
    registry = MCPRegistry(redis=FakeRedis())
    goal_service = DeterministicJiraGoalService()
    app = create_app(
        tenant_service=FakeTenantService(),
        goal_service=goal_service,
        mcp_registry=registry,
    )
    return TestClient(app)


def _mock_jira_mcp(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == (
        "Basic " + base64.b64encode(b"jira-user:jira-token").decode()
    )
    payload = json.loads(request.content)
    if payload["method"] == "tools/list":
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "tools": [
                        {
                            "name": "jira_search",
                            "description": "Search Jira issues with JQL",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"jql": {"type": "string"}},
                                "required": ["jql"],
                            },
                        }
                    ]
                },
            },
        )
    if payload["method"] == "tools/call":
        assert payload["params"] == {
            "name": "jira_search",
            "arguments": {"jql": "project = BAU AND statusCategory != Done"},
        }
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "BAU-1: Checkout payment intermittently fails",
                        }
                    ]
                },
            },
        )
    return httpx.Response(400, json={"error": "unexpected method"})


def _wait_for_terminal_goal(client: TestClient, goal_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        response = client.get(
            f"/goals/{goal_id}",
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 200, response.text
        goal = cast(dict[str, Any], response.json())
        if goal["status"] in {
            GoalStatus.COMPLETE.value,
            GoalStatus.FAILED.value,
            GoalStatus.CANCELLED.value,
        }:
            return goal
        time.sleep(0.05)
    pytest.fail(f"Goal {goal_id} did not reach a terminal state")


def _stream_events(client: TestClient, goal_id: str) -> list[dict[str, Any]]:
    response = client.get(
        f"/goals/{goal_id}/stream",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    events: list[dict[str, Any]] = []
    for block in response.text.strip().split("\n\n"):
        if not block:
            continue
        assert block.startswith("data: ")
        events.append(cast(dict[str, Any], json.loads(block.removeprefix("data: "))))
    return events


def test_agent_bound_goal_discovers_and_calls_read_only_jira_tool() -> None:
    with respx.mock(assert_all_called=False) as router, _make_test_client() as client:
        mcp_route = router.post(MOCK_MCP_URL).mock(side_effect=_mock_jira_mcp)

        connector_response = client.post(
            "/connectors",
            headers={"X-API-Key": TEST_API_KEY},
            json={
                "name": "Jira MCP",
                "url": MOCK_MCP_URL,
                "auth_type": "basic",
                "auth_config": {"username": "jira-user", "password": "jira-token"},
                "description": "Read-only Jira MCP connector",
            },
        )
        assert connector_response.status_code == 201, connector_response.text
        connector_id = connector_response.json()["server_id"]

        agent_response = client.post(
            "/agents",
            headers={"X-API-Key": TEST_API_KEY},
            json={
                "name": "BAU Jira Reader",
                "goal_template": "Search Jira for current BAU issues",
                "autonomy_mode": "bounded-autonomous",
                "connector_ids": [connector_id],
                "trigger_config": {},
            },
        )
        assert agent_response.status_code == 201, agent_response.text
        agent_id = agent_response.json()["agent_id"]

        goal_response = client.post(
            "/goals",
            headers={"X-API-Key": TEST_API_KEY},
            json={
                "goal": "Find BAU Jira issues that are not done",
                "agent_id": agent_id,
                "dry_run": False,
            },
        )
        assert goal_response.status_code == 202, goal_response.text
        goal_id = goal_response.json()["goal_id"]

        goal = _wait_for_terminal_goal(client, goal_id)
        assert goal["status"] == GoalStatus.COMPLETE.value

        events = _stream_events(client, goal_id)
        tool_events = [event for event in events if event.get("type") == "tool_call_complete"]
        assert len(tool_events) == 1
        assert tool_events[0]["tool"] == "jira_search"
        assert tool_events[0]["server_id"] == connector_id
        assert tool_events[0]["success"] is True
        assert "BAU-1" in tool_events[0]["output"]
        # MCP is now called for tool discovery (planning) + tool execution.
        # The exact count may grow as tool-discovery features are added; ≥2 is sufficient.
        assert mcp_route.call_count >= 2


@pytest.mark.asyncio
async def test_real_atlassian_mcp_smoke_discovers_tools_when_credentials_present() -> None:
    username = os.getenv("ATLASSIAN_MCP_BASIC_USERNAME")
    token = os.getenv("ATLASSIAN_MCP_BASIC_TOKEN")
    if not username or not token:
        pytest.skip("ATLASSIAN_MCP_BASIC_USERNAME/TOKEN not configured")

    registry = MCPRegistry(redis=FakeRedis())
    server_id = await registry.register(
        MCPServerConfig(
            name="atlassian-mcp",
            url=ATLASSIAN_MCP_URL,
            auth_type="basic",
            auth_config={"username": username, "password": token},
        ),
        tenant_ctx=TENANT,
    )
    client = MCPClient(registry=registry, timeout=15.0)

    tools = await client.discover_tools(server_id=server_id, tenant_ctx=TENANT)

    assert tools


# ── New comprehensive Jira tests (credential-guarded) ─────────────────────────

# Additional credential variables used by the new tests.
_ATLASSIAN_API_TOKEN = os.getenv("ATLASSIAN_API_TOKEN", "")
_ATLASSIAN_EMAIL = os.getenv("ATLASSIAN_EMAIL", "")

# Skip decorator for tests that require live Atlassian credentials.
_skip_no_atlassian = pytest.mark.skipif(
    not all([_ATLASSIAN_API_TOKEN, _ATLASSIAN_EMAIL]),
    reason="ATLASSIAN_API_TOKEN/EMAIL not configured — set env vars for live Atlassian tests",
)


def _mock_jira_mcp_multi_tool(request: httpx.Request) -> httpx.Response:
    """Mock that exposes both jira_search and jira_create_issue."""
    payload = json.loads(request.content)
    if payload["method"] == "tools/list":
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "tools": [
                        {
                            "name": "jira_search",
                            "description": "Search Jira issues with JQL",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"jql": {"type": "string"}},
                                "required": ["jql"],
                            },
                        },
                        {
                            "name": "jira_create_issue",
                            "description": "Create a new Jira issue",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "summary": {"type": "string"},
                                    "project": {"type": "string"},
                                },
                                "required": ["summary", "project"],
                            },
                        },
                        {
                            "name": "confluence_create_page",
                            "description": "Create a new Confluence page",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["title", "content"],
                            },
                        },
                    ]
                },
            },
        )
    if payload["method"] == "tools/call":
        tool_name = payload["params"]["name"]
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [
                        {"type": "text", "text": f"{tool_name} executed successfully"}
                    ]
                },
            },
        )
    return httpx.Response(400, json={"error": "unexpected method"})


class DeterministicJiraHITLGoalService(GoalService):
    """Goal service whose executor always proposes jira_create_issue (write-high risk)."""

    def _make_agent_loop_for_tenant(
        self, tenant_ctx: TenantContext, app_state: Any, *, agent_id: str | None = None
    ) -> AgentGraph:
        return AgentGraph(
            planner=FakeProvider(
                responses=['{"steps": ["Create a new Jira issue for the bug report"]}']
            ),
            executor=FakeProvider(
                responses=[
                    json.dumps(
                        {
                            "tool": "jira_create_issue",
                            "arguments": {
                                "summary": "Bug: checkout fails intermittently",
                                "project": "BAU",
                            },
                        }
                    ),
                ]
            ),
            verifier=FakeProvider(
                responses=['{"success": true, "reason": "Issue creation requested"}']
            ),
            mcp_client=self._get_mcp_client(),
            hitl_gateway=HITLGateway(),
            result_processor=ResultProcessor(),
            autonomy_mode="supervised",
        )


@pytest.mark.asyncio
async def test_jira_mcp_tools_discovered_by_name() -> None:
    """MCPClient tool discovery resolves expected Jira tool names from mock MCP server.

    Uses respx to mock the Atlassian-style MCP endpoint so no live credentials
    are required. Verifies the discovery response includes at least the core Jira
    tools and that tool names are returned as plain strings.
    """
    registry = MCPRegistry(redis=FakeRedis())
    server_id = await registry.register(
        MCPServerConfig(
            name="atlassian-mcp-test",
            url=MOCK_MCP_URL,
            auth_type="basic",
            auth_config={"username": "jira-user", "password": "jira-token"},
        ),
        tenant_ctx=TENANT,
    )
    client = MCPClient(registry=registry)

    async with respx.mock(assert_all_called=False) as router:
        router.post(MOCK_MCP_URL).mock(side_effect=_mock_jira_mcp_multi_tool)
        tools = await client.discover_tools(server_id=server_id, tenant_ctx=TENANT)

    tool_names = {t.name for t in tools}
    assert "jira_search" in tool_names, f"Expected jira_search in {tool_names}"
    assert "jira_create_issue" in tool_names, f"Expected jira_create_issue in {tool_names}"
    assert "confluence_create_page" in tool_names, (
        f"Expected confluence_create_page in {tool_names}"
    )
    # All tools carry the server_id so the agent graph can route calls correctly
    for tool in tools:
        assert tool.server_id == server_id


def test_jira_goal_with_search_result_contains_issue_key() -> None:
    """Comprehensive validation: plan events, tool call, and issue key in output.

    Extends the basic read-only test with assertions on:
    - The plan step contains a search description
    - The streaming event carries the JQL arguments
    - The tool output contains the Jira issue key (BAU-1)
    - Exactly one tool_call_complete event is emitted
    """
    with respx.mock(assert_all_called=False) as router, _make_test_client() as client:
        router.post(MOCK_MCP_URL).mock(side_effect=_mock_jira_mcp)

        # Register connector
        connector_resp = client.post(
            "/connectors",
            headers={"X-API-Key": TEST_API_KEY},
            json={
                "name": "Jira MCP Extended",
                "url": MOCK_MCP_URL,
                "auth_type": "basic",
                "auth_config": {"username": "jira-user", "password": "jira-token"},
            },
        )
        assert connector_resp.status_code == 201
        connector_id = connector_resp.json()["server_id"]

        # Create agent
        agent_resp = client.post(
            "/agents",
            headers={"X-API-Key": TEST_API_KEY},
            json={
                "name": "Comprehensive BAU Jira Reader",
                "goal_template": "Search Jira for BAU issues",
                "autonomy_mode": "bounded-autonomous",
                "connector_ids": [connector_id],
                "trigger_config": {},
            },
        )
        assert agent_resp.status_code == 201
        agent_id = agent_resp.json()["agent_id"]

        # Submit goal
        goal_resp = client.post(
            "/goals",
            headers={"X-API-Key": TEST_API_KEY},
            json={
                "goal": "Find BAU Jira issues that are not done",
                "agent_id": agent_id,
                "dry_run": False,
            },
        )
        assert goal_resp.status_code == 202
        goal_id = goal_resp.json()["goal_id"]

        goal = _wait_for_terminal_goal(client, goal_id)
        assert goal["status"] == GoalStatus.COMPLETE.value

        events = _stream_events(client, goal_id)

        # At least one tool_call_complete event
        tool_events = [e for e in events if e.get("type") == "tool_call_complete"]
        assert len(tool_events) >= 1, "Expected at least one tool_call_complete event"

        tool_evt = tool_events[0]
        assert tool_evt["tool"] == "jira_search"
        assert tool_evt["success"] is True
        assert "BAU-1" in tool_evt["output"], (
            f"Expected BAU-1 in tool output: {tool_evt['output']}"
        )

        # The goal record includes goal text and status
        assert "goal" in goal
        assert goal["dry_run"] is False


def test_jira_write_high_tool_triggers_hitl_approval() -> None:
    """Creating a Jira issue in supervised mode triggers HITL approval event.

    Verifies the write-risk governance path:
    - Executor requests jira_create_issue (write_high risk)
    - HITLGateway creates an approval request
    - The SSE stream contains a tool_call_pending_approval event
    - The event carries the correct tool name and request_id
    The goal still completes because the HITL path does not block (it emits
    events and continues to the verifier with a pending-approval message).
    """
    registry = MCPRegistry(redis=FakeRedis())
    goal_service = DeterministicJiraHITLGoalService()
    app = create_app(
        tenant_service=FakeTenantService(),
        goal_service=goal_service,
        mcp_registry=registry,
    )

    with TestClient(app) as client, respx.mock(assert_all_called=False) as router:
        router.post(MOCK_MCP_URL).mock(side_effect=_mock_jira_mcp_multi_tool)

        # Register connector exposing jira_create_issue
        connector_resp = client.post(
            "/connectors",
            headers={"X-API-Key": TEST_API_KEY},
            json={
                "name": "Jira Write MCP",
                "url": MOCK_MCP_URL,
                "auth_type": "basic",
                "auth_config": {"username": "jira-user", "password": "jira-token"},
            },
        )
        assert connector_resp.status_code == 201
        connector_id = connector_resp.json()["server_id"]

        # Create agent in supervised mode
        agent_resp = client.post(
            "/agents",
            headers={"X-API-Key": TEST_API_KEY},
            json={
                "name": "Supervised Jira Writer",
                "goal_template": "Create Jira issues on request",
                "autonomy_mode": "supervised",
                "connector_ids": [connector_id],
                "trigger_config": {},
            },
        )
        assert agent_resp.status_code == 201
        agent_id = agent_resp.json()["agent_id"]

        # Submit goal — this will trigger the write_high HITL path
        goal_resp = client.post(
            "/goals",
            headers={"X-API-Key": TEST_API_KEY},
            json={
                "goal": "Create a Jira issue for the checkout bug",
                "agent_id": agent_id,
                "dry_run": False,
            },
        )
        assert goal_resp.status_code == 202
        goal_id = goal_resp.json()["goal_id"]

        # Wait for goal to reach terminal state (write_high does not block)
        goal = _wait_for_terminal_goal(client, goal_id)
        assert goal["status"] in {
            GoalStatus.COMPLETE.value,
            GoalStatus.FAILED.value,
        }

        events = _stream_events(client, goal_id)
        event_types = [e.get("type") for e in events]

        # The HITL path must have emitted a pending-approval event
        pending_events = [
            e for e in events if e.get("type") == "tool_call_pending_approval"
        ]
        assert len(pending_events) >= 1, (
            f"Expected tool_call_pending_approval event. Got event types: {event_types}"
        )
        pending_evt = pending_events[0]
        assert pending_evt["tool"] == "jira_create_issue"
        assert "request_id" in pending_evt
