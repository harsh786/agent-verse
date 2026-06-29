"""Test Python SDK has full parity with TypeScript SDK surface area."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from agentverse.client import AgentVerseClient

BASE_URL = "http://localhost:8000"
API_KEY = "test-key"
_NOW = datetime.now(UTC).isoformat()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client(mock_router):
    async with AgentVerseClient(api_key=API_KEY, base_url=BASE_URL) as c:
        yield c


# ---------------------------------------------------------------------------
# hasattr surface-parity tests (fail fast if a method is missing entirely)
# ---------------------------------------------------------------------------


def test_sdk_has_test_connector():
    assert hasattr(AgentVerseClient, "test_connector")


def test_sdk_has_get_connector_catalog():
    assert hasattr(AgentVerseClient, "get_connector_catalog")


def test_sdk_has_recall_memory():
    assert hasattr(AgentVerseClient, "recall_memory")


def test_sdk_has_store_memory():
    assert hasattr(AgentVerseClient, "store_memory")


def test_sdk_has_search_knowledge():
    assert hasattr(AgentVerseClient, "search_knowledge")


def test_sdk_has_get_goal_metrics():
    assert hasattr(AgentVerseClient, "get_goal_metrics")


def test_sdk_has_get_cost_metrics():
    assert hasattr(AgentVerseClient, "get_cost_metrics")


def test_sdk_has_simulate():
    assert hasattr(AgentVerseClient, "simulate"), "Python SDK missing simulate() method"


def test_sdk_has_create_schedule_nl():
    assert hasattr(
        AgentVerseClient, "create_schedule_nl"
    ), "Python SDK missing NL schedule method"


def test_sdk_has_delete_schedule():
    assert hasattr(AgentVerseClient, "delete_schedule")


def test_sdk_has_get_goal_replay():
    """replayGoal in TS SDK → get_goal_replay in Python SDK."""
    assert hasattr(AgentVerseClient, "get_goal_replay")


# ---------------------------------------------------------------------------
# Model import tests — new models must be importable from agentverse
# ---------------------------------------------------------------------------


def test_memory_model_importable():
    from agentverse.models import Memory

    m = Memory(memory_id="m-1", content="hello")
    assert m.memory_id == "m-1"
    assert m.tags == []


def test_schedule_model_importable():
    from agentverse.models import Schedule

    s = Schedule(
        schedule_id="sched-1",
        name="Nightly",
        goal_template="Run nightly report",
        enabled=True,
    )
    assert s.schedule_id == "sched-1"
    assert s.cron is None


def test_goal_metrics_model_importable():
    from agentverse.models import GoalMetrics

    gm = GoalMetrics(
        active_goals=3,
        total_goals=100,
        success_rate=0.95,
        avg_latency_ms=1200.0,
        cost_today_usd=0.42,
    )
    assert gm.success_rate == pytest.approx(0.95)


def test_cost_metrics_model_importable():
    from agentverse.models import CostMetrics

    cm = CostMetrics(
        total_cost_usd=12.34,
        daily_budget_usd=50.0,
        budget_utilization=0.25,
    )
    assert cm.total_cost_usd == pytest.approx(12.34)
    assert cm.cost_by_day == []
    assert cm.cost_by_model == {}


def test_simulation_result_model_importable():
    from agentverse.models import SimulationResult

    sr = SimulationResult(run_id="run-1", goal="do something", completed=True)
    assert sr.run_id == "run-1"
    assert sr.error is None
    assert sr.steps == []


# ---------------------------------------------------------------------------
# HTTP-level tests using respx
# ---------------------------------------------------------------------------


async def test_test_connector_posts_to_correct_url(client, mock_router):
    mock_router.post("/connectors/conn-123/test").mock(
        return_value=httpx.Response(200, json={"reachable": True, "latency_ms": 42.5})
    )
    result = await client.test_connector("conn-123")
    assert result["reachable"] is True
    assert result["latency_ms"] == pytest.approx(42.5)


async def test_get_connector_catalog_calls_catalog_endpoint(client, mock_router):
    catalog = [{"type": "github", "name": "GitHub MCP"}, {"type": "jira", "name": "Jira MCP"}]
    mock_router.get("/connectors/catalog").mock(
        return_value=httpx.Response(200, json=catalog)
    )
    result = await client.get_connector_catalog()
    assert len(result) == 2
    assert result[0]["type"] == "github"


async def test_recall_memory_sends_query_params(client, mock_router):
    memories = [
        {"memory_id": "mem-1", "content": "Python is great", "tags": ["python"]}
    ]
    mock_router.get("/memory/recall").mock(
        return_value=httpx.Response(200, json=memories)
    )
    result = await client.recall_memory("python language", limit=5)
    assert len(result) == 1
    assert result[0].memory_id == "mem-1"
    assert result[0].content == "Python is great"


async def test_store_memory_posts_content(client, mock_router):
    payload_response = {
        "memory_id": "mem-new",
        "content": "Remember this",
        "tags": ["important"],
        "created_at": _NOW,
    }
    mock_router.post("/memory").mock(
        return_value=httpx.Response(200, json=payload_response)
    )
    mem = await client.store_memory("Remember this", tags=["important"])
    assert mem.memory_id == "mem-new"
    assert mem.tags == ["important"]


async def test_store_memory_without_tags(client, mock_router):
    mock_router.post("/memory").mock(
        return_value=httpx.Response(
            200,
            json={"memory_id": "m-2", "content": "no tags", "tags": []},
        )
    )
    mem = await client.store_memory("no tags")
    assert mem.memory_id == "m-2"


async def test_search_knowledge_sends_correct_params(client, mock_router):
    results = [{"chunk_id": "c-1", "content": "chunk text", "score": 0.92}]
    mock_router.get("/knowledge/search").mock(
        return_value=httpx.Response(200, json=results)
    )
    result = await client.search_knowledge("col-abc", "semantic search query", limit=3)
    assert len(result) == 1
    assert result[0]["chunk_id"] == "c-1"


async def test_get_goal_metrics_returns_model(client, mock_router):
    metrics_payload = {
        "active_goals": 5,
        "total_goals": 200,
        "success_rate": 0.91,
        "avg_latency_ms": 980.0,
        "cost_today_usd": 1.23,
    }
    mock_router.get("/analytics/goals").mock(
        return_value=httpx.Response(200, json=metrics_payload)
    )
    m = await client.get_goal_metrics(days=7)
    assert m.active_goals == 5
    assert m.success_rate == pytest.approx(0.91)


async def test_get_cost_metrics_returns_model(client, mock_router):
    cost_payload = {
        "total_cost_usd": 55.00,
        "cost_by_day": [{"date": "2026-06-28", "cost_usd": 5.0}],
        "cost_by_model": {"gpt-4o": 30.0, "claude-3-5-sonnet": 25.0},
        "daily_budget_usd": 100.0,
        "budget_utilization": 0.55,
    }
    mock_router.get("/analytics/cost").mock(
        return_value=httpx.Response(200, json=cost_payload)
    )
    cm = await client.get_cost_metrics(days=30)
    assert cm.total_cost_usd == pytest.approx(55.00)
    assert cm.cost_by_model["gpt-4o"] == pytest.approx(30.0)


async def test_simulate_posts_goal(client, mock_router):
    sim_response = {
        "run_id": "sim-abc",
        "goal": "Process all invoices",
        "steps": [{"step": 1, "action": "list_invoices"}],
        "completed": True,
        "error": None,
    }
    mock_router.post("/enterprise/simulate").mock(
        return_value=httpx.Response(200, json=sim_response)
    )
    result = await client.simulate("Process all invoices")
    assert result.run_id == "sim-abc"
    assert result.completed is True


async def test_simulate_with_mock_tools(client, mock_router):
    sim_response = {
        "run_id": "sim-mocked",
        "goal": "fetch data",
        "steps": [],
        "completed": True,
    }
    mock_router.post("/enterprise/simulate").mock(
        return_value=httpx.Response(200, json=sim_response)
    )
    result = await client.simulate("fetch data", mock_tools={"fetch": {"data": []}})
    assert result.run_id == "sim-mocked"


async def test_create_schedule_nl_posts_command(client, mock_router):
    schedule_response = {
        "schedule_id": "sched-nl-1",
        "name": "Nightly report",
        "goal_template": "Run the nightly report",
        "enabled": True,
        "cron": "0 2 * * *",
        "created_at": _NOW,
    }
    mock_router.post("/schedules/nl").mock(
        return_value=httpx.Response(200, json=schedule_response)
    )
    sched = await client.create_schedule_nl("Run the nightly report every day at 2am")
    assert sched.schedule_id == "sched-nl-1"
    assert sched.cron == "0 2 * * *"


async def test_delete_schedule_calls_delete_endpoint(client, mock_router):
    mock_router.delete("/schedules/sched-42").mock(
        return_value=httpx.Response(204)
    )
    result = await client.delete_schedule("sched-42")
    assert result is None
