"""Tests for Society — civilization membership, reputation EWMA, routing."""
import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from app.civilization.society import Society, _REPUTATION_EWMA_ALPHA


def _make_society(**kwargs) -> Society:
    return Society(
        civilization_id="civ-1",
        tenant_id="t1",
        db_session_factory=kwargs.get("db"),
        agent_router=kwargs.get("router"),
        bus=kwargs.get("bus"),
    )


@pytest.mark.asyncio
async def test_reputation_ewma_math():
    society = _make_society()
    society._members["agent-1"] = {
        "reputation": 0.5,
        "status": "active",
        "depth": 0,
        "budget_spent_usd": 0,
        "goal_template": "",
        "role": "worker",
    }
    society._persist_reputation = AsyncMock()

    new_rep = await society.update_reputation(agent_id="agent-1", new_score=1.0)
    # EWMA: 0.2 * 1.0 + 0.8 * 0.5 = 0.6
    assert abs(new_rep - 0.6) < 0.001


@pytest.mark.asyncio
async def test_reputation_seed_is_0_5():
    society = _make_society()
    # Agent not in cache → default 0.5
    rep = await society._get_current_reputation("unknown-agent")
    assert rep == 0.5


@pytest.mark.asyncio
async def test_reputation_ewma_multiple_updates():
    society = _make_society()
    society._persist_reputation = AsyncMock()

    # Start at 0.5, apply three poor scores
    society._members["a1"] = {
        "reputation": 0.5,
        "status": "active",
        "depth": 0,
        "budget_spent_usd": 0,
        "role": "worker",
    }
    await society.update_reputation(agent_id="a1", new_score=0.1)
    await society.update_reputation(agent_id="a1", new_score=0.1)
    await society.update_reputation(agent_id="a1", new_score=0.1)
    # Reputation should decay significantly below 0.5
    assert society._members["a1"]["reputation"] < 0.5


@pytest.mark.asyncio
async def test_load_members_from_db():
    # Use MagicMock so self._db() returns mock_session synchronously
    # (matches governor test pattern — AsyncMock would return a coroutine)
    from unittest.mock import MagicMock

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    now = datetime.now(UTC)
    mock_session.execute = AsyncMock(
        return_value=AsyncMock(
            fetchall=lambda: [
                ("m1", "agent-1", "worker", None, 0.7, "active", 1, 10.0, 2.5, now, now),
                (
                    "m2",
                    "agent-2",
                    "worker",
                    "agent-1",
                    0.3,
                    "idle",
                    2,
                    5.0,
                    1.0,
                    now,
                    now,
                ),
            ]
        )
    )
    mock_db = MagicMock(return_value=mock_session)

    society = _make_society(db=mock_db)
    members = await society.load_members()

    assert len(members) == 2
    assert members[0]["reputation"] == 0.7
    assert members[1]["parent_agent_id"] == "agent-1"


@pytest.mark.asyncio
async def test_route_goal_picks_highest_reputation():
    society = _make_society()
    society._members = {
        "low-rep": {
            "agent_id": "low-rep",
            "reputation": 0.3,
            "status": "active",
            "depth": 0,
            "budget_spent_usd": 0,
            "goal_template": "",
            "role": "worker",
        },
        "high-rep": {
            "agent_id": "high-rep",
            "reputation": 0.9,
            "status": "active",
            "depth": 0,
            "budget_spent_usd": 0,
            "goal_template": "",
            "role": "worker",
        },
    }

    from app.tenancy.context import PlanTier, TenantContext

    tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")

    routing = await society.route_goal(goal="do something", tenant_ctx=tenant_ctx)
    assert routing["agent_id"] == "high-rep"


@pytest.mark.asyncio
async def test_route_goal_returns_needs_new_agent_when_empty():
    society = _make_society()

    from app.tenancy.context import PlanTier, TenantContext

    tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")

    routing = await society.route_goal(goal="do something", tenant_ctx=tenant_ctx)
    assert routing["mode"] == "needs_new_agent"


@pytest.mark.asyncio
async def test_get_lineage_graph_structure():
    society = _make_society()
    society._members = {
        "parent": {
            "agent_id": "parent",
            "reputation": 0.8,
            "status": "active",
            "depth": 0,
            "parent_agent_id": None,
            "budget_spent_usd": 5.0,
            "role": "coordinator",
        },
        "child": {
            "agent_id": "child",
            "reputation": 0.6,
            "status": "active",
            "depth": 1,
            "parent_agent_id": "parent",
            "budget_spent_usd": 2.0,
            "role": "worker",
        },
    }
    graph = await society.get_lineage_graph()

    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["source"] == "parent"
    assert graph["edges"][0]["target"] == "child"
    assert graph["edges"][0]["type"] == "spawn_lineage"


@pytest.mark.asyncio
async def test_reputation_clamped_to_unit_interval():
    """Reputation input clamping: scores > 1.0 treated as 1.0, < 0.0 as 0.0."""
    society = _make_society()
    society._members["a1"] = {
        "reputation": 0.5,
        "status": "active",
        "depth": 0,
        "budget_spent_usd": 0,
        "role": "worker",
    }
    society._persist_reputation = AsyncMock()

    # Score above 1.0 should be clamped
    rep = await society.update_reputation(agent_id="a1", new_score=2.0)
    # Equivalent to new_score=1.0: 0.2 * 1.0 + 0.8 * 0.5 = 0.6
    assert abs(rep - 0.6) < 0.001


@pytest.mark.asyncio
async def test_update_budget_spent_accumulates():
    society = _make_society()
    society._members["a1"] = {
        "agent_id": "a1",
        "reputation": 0.5,
        "status": "active",
        "depth": 0,
        "budget_spent_usd": 1.0,
        "role": "worker",
    }

    await society.update_budget_spent("a1", 3.5)
    assert society._members["a1"]["budget_spent_usd"] == pytest.approx(4.5)


@pytest.mark.asyncio
async def test_update_member_status_in_memory():
    society = _make_society()
    society._members["a1"] = {
        "agent_id": "a1",
        "reputation": 0.5,
        "status": "active",
        "depth": 0,
        "budget_spent_usd": 0,
        "role": "worker",
    }

    await society.update_member_status("a1", "debating")
    assert society._members["a1"]["status"] == "debating"


@pytest.mark.asyncio
async def test_get_metrics_empty_society():
    society = _make_society()
    metrics = await society.get_metrics()
    assert metrics["total_members"] == 0
    assert metrics["avg_reputation"] == 0.5  # default seed


@pytest.mark.asyncio
async def test_get_metrics_with_members():
    society = _make_society()
    society._members = {
        "a1": {
            "agent_id": "a1",
            "reputation": 0.8,
            "status": "active",
            "depth": 0,
            "budget_spent_usd": 5.0,
            "role": "worker",
        },
        "a2": {
            "agent_id": "a2",
            "reputation": 0.4,
            "status": "idle",
            "depth": 1,
            "budget_spent_usd": 2.0,
            "role": "worker",
        },
    }
    metrics = await society.get_metrics()
    assert metrics["total_members"] == 2
    assert metrics["active_members"] == 1
    assert metrics["idle_members"] == 1
    assert metrics["total_budget_spent_usd"] == pytest.approx(7.0)
    assert metrics["avg_reputation"] == pytest.approx(0.6)
