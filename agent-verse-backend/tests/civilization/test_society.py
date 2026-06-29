"""Tests for Society — civilization membership, reputation EWMA, routing."""
import pytest
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.civilization.society import Society, _REPUTATION_EWMA_ALPHA


# ── helpers ────────────────────────────────────────────────────────────────────


class _noop_ctx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class _FakeSession:
    def __init__(self, rows=None, raise_on=None):
        self.executions = []
        self._rows = rows or []
        self._raise = raise_on

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def begin(self):
        return _noop_ctx()

    async def execute(self, stmt, params=None):
        if self._raise:
            raise RuntimeError(self._raise)
        self.executions.append((stmt, params))
        return SimpleNamespace(
            fetchall=lambda: list(self._rows),
            fetchone=lambda: self._rows[0] if self._rows else None,
        )


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


# ── Additional coverage tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_member_from_cache():
    """get_member returns from in-memory cache without DB hit."""
    society = _make_society()
    society._members["a1"] = {
        "agent_id": "a1",
        "reputation": 0.7,
        "status": "active",
        "depth": 0,
        "budget_spent_usd": 2.0,
        "role": "worker",
    }
    result = await society.get_member("a1")
    assert result is not None
    assert result["agent_id"] == "a1"


@pytest.mark.asyncio
async def test_get_member_from_db_hit():
    now = datetime.now(UTC)
    row = ("m1", "agent-db", "worker", None, 0.65, "active", 1, 8.0, 1.5, now, now)
    session = _FakeSession(rows=[row])
    society = _make_society(db=lambda: session)

    result = await society.get_member("agent-db")
    assert result is not None
    assert result["agent_id"] == "agent-db"
    assert abs(result["reputation"] - 0.65) < 0.001
    # Should be cached now
    assert "agent-db" in society._members


@pytest.mark.asyncio
async def test_get_member_from_db_miss():
    session = _FakeSession(rows=[])
    society = _make_society(db=lambda: session)
    result = await society.get_member("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_member_db_exception_returns_none():
    session = _FakeSession(raise_on="DB error")
    society = _make_society(db=lambda: session)
    result = await society.get_member("agent-err")
    assert result is None


@pytest.mark.asyncio
async def test_get_member_not_in_cache_no_db():
    society = _make_society()
    result = await society.get_member("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_update_reputation_with_bus_publishes():
    society = _make_society()
    society._members["a1"] = {
        "reputation": 0.5,
        "status": "active",
        "depth": 0,
        "budget_spent_usd": 0,
        "role": "worker",
    }
    mock_bus = AsyncMock()
    mock_bus.publish = AsyncMock()
    society._bus = mock_bus
    society._persist_reputation = AsyncMock()

    await society.update_reputation(agent_id="a1", new_score=0.9)
    mock_bus.publish.assert_called_once()
    call_kwargs = mock_bus.publish.call_args.kwargs
    assert call_kwargs["topic"] == "lifecycle"
    assert call_kwargs["payload"]["event"] == "reputation_updated"


@pytest.mark.asyncio
async def test_update_reputation_bus_exception_swallowed():
    society = _make_society()
    society._members["a1"] = {
        "reputation": 0.5,
        "status": "active",
        "depth": 0,
        "budget_spent_usd": 0,
        "role": "worker",
    }
    mock_bus = AsyncMock()
    mock_bus.publish = AsyncMock(side_effect=RuntimeError("Bus down"))
    society._bus = mock_bus
    society._persist_reputation = AsyncMock()

    # Should not raise
    rep = await society.update_reputation(agent_id="a1", new_score=0.9)
    assert rep > 0.5  # EWMA result still returned


@pytest.mark.asyncio
async def test_update_member_status_with_db():
    session = _FakeSession()
    society = _make_society(db=lambda: session)
    society._members["a1"] = {"status": "active"}
    await society.update_member_status("a1", "idle")
    assert society._members["a1"]["status"] == "idle"
    assert len(session.executions) >= 1


@pytest.mark.asyncio
async def test_update_member_status_db_exception_swallowed():
    session = _FakeSession(raise_on="DB error")
    society = _make_society(db=lambda: session)
    society._members["a1"] = {"status": "active"}
    await society.update_member_status("a1", "failed")  # should not raise
    # In-memory update still happens
    assert society._members["a1"]["status"] == "failed"


@pytest.mark.asyncio
async def test_update_budget_spent_with_db():
    session = _FakeSession()
    society = _make_society(db=lambda: session)
    society._members["a1"] = {"budget_spent_usd": 5.0}
    await society.update_budget_spent("a1", 2.5)
    assert len(session.executions) >= 1
    assert abs(society._members["a1"]["budget_spent_usd"] - 7.5) < 0.001


@pytest.mark.asyncio
async def test_update_budget_spent_db_exception_swallowed():
    session = _FakeSession(raise_on="DB error")
    society = _make_society(db=lambda: session)
    society._members["a1"] = {"budget_spent_usd": 3.0}
    await society.update_budget_spent("a1", 1.0)  # should not raise


@pytest.mark.asyncio
async def test_route_goal_with_router_success():
    from app.tenancy.context import PlanTier, TenantContext

    mock_routing_result = MagicMock()
    mock_routing_result.agent_id = "router-chosen"
    mock_routing_result.mode = "single_agent"
    mock_routing_result.reason = "best match"
    mock_routing_result.confidence = 0.8

    mock_router = AsyncMock()
    mock_router.route = AsyncMock(return_value=mock_routing_result)

    society = _make_society(router=mock_router)
    society._members = {
        "router-chosen": {
            "agent_id": "router-chosen",
            "reputation": 0.85,
            "status": "active",
            "depth": 0,
            "budget_spent_usd": 0,
            "goal_template": "",
            "role": "worker",
        }
    }

    tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")
    routing = await society.route_goal(goal="complex task", tenant_ctx=tenant_ctx)
    assert routing["agent_id"] == "router-chosen"
    assert routing["mode"] == "single_agent"
    # Confidence should be boosted by reputation
    assert routing["confidence"] > 0.8 * 0.5  # at least > 0 since reputation boosts


@pytest.mark.asyncio
async def test_route_goal_with_router_result_not_in_members():
    from app.tenancy.context import PlanTier, TenantContext

    mock_routing_result = MagicMock()
    mock_routing_result.agent_id = "external-agent"
    mock_routing_result.mode = "single_agent"
    mock_routing_result.reason = "matched"
    mock_routing_result.confidence = 0.7

    mock_router = AsyncMock()
    mock_router.route = AsyncMock(return_value=mock_routing_result)

    society = _make_society(router=mock_router)
    society._members = {
        "local-agent": {
            "agent_id": "local-agent",
            "reputation": 0.8,
            "status": "active",
            "depth": 0,
            "budget_spent_usd": 0,
            "goal_template": "",
            "role": "worker",
        }
    }

    tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")
    routing = await society.route_goal(goal="task", tenant_ctx=tenant_ctx)
    assert routing["agent_id"] == "external-agent"
    assert routing["confidence"] == 0.7  # no reputation boost since not in members


@pytest.mark.asyncio
async def test_route_goal_with_router_exception_falls_back():
    from app.tenancy.context import PlanTier, TenantContext

    mock_router = AsyncMock()
    mock_router.route = AsyncMock(side_effect=RuntimeError("Router failed"))

    society = _make_society(router=mock_router)
    society._members = {
        "fallback-agent": {
            "agent_id": "fallback-agent",
            "reputation": 0.75,
            "status": "active",
            "depth": 0,
            "budget_spent_usd": 0,
            "goal_template": "",
            "role": "worker",
        }
    }

    tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")
    routing = await society.route_goal(goal="some task", tenant_ctx=tenant_ctx)
    # Falls back to highest reputation
    assert routing["agent_id"] == "fallback-agent"
    assert routing["mode"] == "single_agent"


@pytest.mark.asyncio
async def test_get_current_reputation_from_db():
    session = _FakeSession(rows=[(0.72,)])
    society = _make_society(db=lambda: session)
    rep = await society._get_current_reputation("agent-db")
    assert abs(rep - 0.72) < 0.001


@pytest.mark.asyncio
async def test_get_current_reputation_db_returns_none_value():
    session = _FakeSession(rows=[(None,)])
    society = _make_society(db=lambda: session)
    rep = await society._get_current_reputation("agent-db")
    assert rep == 0.5  # default when DB value is None


@pytest.mark.asyncio
async def test_get_current_reputation_db_exception_returns_default():
    session = _FakeSession(raise_on="DB error")
    society = _make_society(db=lambda: session)
    rep = await society._get_current_reputation("agent-db")
    assert rep == 0.5


@pytest.mark.asyncio
async def test_get_current_reputation_db_no_row_returns_default():
    session = _FakeSession(rows=[])
    society = _make_society(db=lambda: session)
    rep = await society._get_current_reputation("nonexistent")
    assert rep == 0.5


@pytest.mark.asyncio
async def test_persist_reputation_with_db():
    session = _FakeSession()
    society = _make_society(db=lambda: session)
    await society._persist_reputation("a1", 0.75)
    assert len(session.executions) >= 1


@pytest.mark.asyncio
async def test_persist_reputation_no_db():
    society = _make_society()
    await society._persist_reputation("a1", 0.75)  # no-op


@pytest.mark.asyncio
async def test_persist_reputation_db_exception_swallowed():
    session = _FakeSession(raise_on="DB error")
    society = _make_society(db=lambda: session)
    await society._persist_reputation("a1", 0.75)  # should not raise


@pytest.mark.asyncio
async def test_load_members_db_exception_returns_cache():
    """If DB fails, load_members returns in-memory cache."""
    session = _FakeSession(raise_on="DB error")
    society = _make_society(db=lambda: session)
    society._members["cached"] = {
        "agent_id": "cached",
        "reputation": 0.5,
        "status": "active",
        "depth": 0,
        "budget_spent_usd": 0,
        "role": "worker",
    }
    members = await society.load_members()
    assert len(members) == 1
    assert members[0]["agent_id"] == "cached"


@pytest.mark.asyncio
async def test_get_metrics_retired_counted():
    society = _make_society()
    society._members = {
        "a1": {"agent_id": "a1", "reputation": 0.8, "status": "retired", "budget_spent_usd": 2.0},
    }
    metrics = await society.get_metrics()
    assert metrics["retired_members"] == 1
    assert metrics["total_members"] == 1


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
