"""Unit tests for AgentVerseClient."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from agentverse.client import AgentVerseClient
from agentverse.exceptions import AuthError, GoalFailedError, GoalTimeoutError, NotFoundError
from agentverse.models import GoalStatus

BASE_URL = "http://localhost:8000"
API_KEY = "test-key"

_NOW = datetime.now(UTC).isoformat()

_GOAL_PAYLOAD = {
    "goal_id": "goal-abc",
    "goal": "Run a report",
    "status": "pending",
    "created_at": _NOW,
    "steps_total": 0,
    "steps_completed": 0,
    "cost_usd": 0.0,
}

_AGENT_PAYLOAD = {
    "agent_id": "agent-xyz",
    "name": "ReportBot",
    "autonomy_mode": "supervised",
    "created_at": _NOW,
}

_CONNECTOR_PAYLOAD = {
    "server_id": "conn-1",
    "name": "Jira",
    "url": "http://jira-mcp:8080",
    "status": "active",
    "created_at": _NOW,
}


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client(mock_router):
    async with AgentVerseClient(api_key=API_KEY, base_url=BASE_URL) as c:
        yield c


# ---- Instantiation ----

def test_empty_api_key_raises():
    with pytest.raises(AuthError):
        AgentVerseClient(api_key="")


# ---- Goals ----

async def test_submit_goal(client, mock_router):
    mock_router.post("/goals").mock(return_value=httpx.Response(200, json=_GOAL_PAYLOAD))
    goal = await client.submit_goal("Run a report")
    assert goal.goal_id == "goal-abc"
    assert goal.status == GoalStatus.PENDING


async def test_submit_goal_401_raises_auth_error(client, mock_router):
    mock_router.post("/goals").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError):
        await client.submit_goal("Test")


async def test_get_goal(client, mock_router):
    mock_router.get("/goals/goal-abc").mock(return_value=httpx.Response(200, json=_GOAL_PAYLOAD))
    goal = await client.get_goal("goal-abc")
    assert goal.goal_id == "goal-abc"


async def test_get_goal_404_raises(client, mock_router):
    mock_router.get("/goals/missing").mock(return_value=httpx.Response(404, text="not found"))
    with pytest.raises(NotFoundError):
        await client.get_goal("missing")


async def test_wait_for_goal_completes(client, mock_router):
    completed = {**_GOAL_PAYLOAD, "status": "completed", "result": "Done!"}
    mock_router.get("/goals/goal-abc").mock(return_value=httpx.Response(200, json=completed))
    goal = await client.wait_for_goal("goal-abc", timeout=10.0)
    assert goal.status == GoalStatus.COMPLETED
    assert goal.result == "Done!"


async def test_wait_for_goal_failed_raises(client, mock_router):
    failed = {**_GOAL_PAYLOAD, "status": "failed", "error": "LLM quota exceeded"}
    mock_router.get("/goals/goal-abc").mock(return_value=httpx.Response(200, json=failed))
    with pytest.raises(GoalFailedError) as exc_info:
        await client.wait_for_goal("goal-abc", timeout=10.0)
    assert "quota exceeded" in str(exc_info.value)


async def test_cancel_goal(client, mock_router):
    cancelled = {**_GOAL_PAYLOAD, "status": "cancelled"}
    mock_router.post("/goals/goal-abc/cancel").mock(
        return_value=httpx.Response(200, json=cancelled)
    )
    goal = await client.cancel_goal("goal-abc")
    assert goal.status == GoalStatus.CANCELLED


async def test_list_goals(client, mock_router):
    mock_router.get("/goals").mock(
        return_value=httpx.Response(200, json={"goals": [_GOAL_PAYLOAD]})
    )
    goals = await client.list_goals()
    assert len(goals) == 1
    assert goals[0].goal_id == "goal-abc"


# ---- Agents ----

async def test_create_agent(client, mock_router):
    mock_router.post("/agents").mock(return_value=httpx.Response(200, json=_AGENT_PAYLOAD))
    agent = await client.create_agent("ReportBot")
    assert agent.name == "ReportBot"


async def test_list_agents(client, mock_router):
    mock_router.get("/agents").mock(return_value=httpx.Response(200, json=[_AGENT_PAYLOAD]))
    agents = await client.list_agents()
    assert len(agents) == 1


async def test_delete_agent(client, mock_router):
    mock_router.delete("/agents/agent-xyz").mock(return_value=httpx.Response(204))
    await client.delete_agent("agent-xyz")  # should not raise


# ---- Connectors ----

async def test_register_connector(client, mock_router):
    mock_router.post("/connectors").mock(
        return_value=httpx.Response(200, json=_CONNECTOR_PAYLOAD)
    )
    conn = await client.register_connector("Jira", "http://jira-mcp:8080")
    assert conn.server_id == "conn-1"


async def test_list_connectors(client, mock_router):
    mock_router.get("/connectors").mock(
        return_value=httpx.Response(200, json=[_CONNECTOR_PAYLOAD])
    )
    conns = await client.list_connectors()
    assert len(conns) == 1
    assert conns[0].name == "Jira"
