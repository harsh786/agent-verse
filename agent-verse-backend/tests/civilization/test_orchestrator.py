"""Tests for CivilizationOrchestrator — runtime loop, goal dispatch, debate, tick."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.civilization.orchestrator import CivilizationOrchestrator
from app.civilization.models import Constitution


def _make_orchestrator(**kwargs) -> CivilizationOrchestrator:
    constitution = kwargs.get("constitution", Constitution(max_depth=3, total_budget_usd=50.0))

    mock_society = AsyncMock()
    mock_society.route_goal = AsyncMock(
        return_value={
            "mode": "single_agent",
            "agent_id": "a1",
            "confidence": 0.8,
            "reason": "best match",
        }
    )
    mock_society.load_members = AsyncMock(return_value=[])
    mock_society.get_metrics = AsyncMock(return_value={"total_members": 1, "active_members": 1})
    mock_society.get_lineage_graph = AsyncMock(return_value={"nodes": [], "edges": []})
    mock_society.update_member_status = AsyncMock()

    mock_governor = AsyncMock()
    mock_governor.is_paused_sync = MagicMock(return_value=False)
    mock_governor.check_breach = AsyncMock(
        return_value=MagicMock(breached=False, reasons=[])
    )
    mock_governor.auto_retire_idle = AsyncMock(return_value=[])

    mock_bus = AsyncMock()
    mock_bus.publish = AsyncMock()

    return CivilizationOrchestrator(
        civilization_id="civ-1",
        tenant_id="t1",
        constitution=constitution,
        governor=kwargs.get("governor", mock_governor),
        society=kwargs.get("society", mock_society),
        bus=kwargs.get("bus", mock_bus),
        blackboard=kwargs.get("blackboard", AsyncMock()),
        goal_service=kwargs.get("goal_service"),
        db_session_factory=kwargs.get("db"),
        redis=kwargs.get("redis"),
    )


@pytest.mark.asyncio
async def test_submit_goal_accepted():
    orch = _make_orchestrator()
    orch._goal_service = AsyncMock()
    orch._goal_service.submit_goal = AsyncMock(return_value={"goal_id": "g1"})

    from app.tenancy.context import PlanTier, TenantContext

    orch._tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")

    result = await orch.submit_goal("Analyze all Jira issues")
    assert result["status"] == "accepted"


@pytest.mark.asyncio
async def test_submit_goal_rejected_when_paused():
    mock_gov = AsyncMock()
    mock_gov.is_paused_sync = MagicMock(return_value=True)

    import sys

    sys.modules.setdefault(
        "app.scaling.tasks",
        MagicMock(_get_sync_redis=MagicMock(return_value=MagicMock())),
    )

    orch = _make_orchestrator(governor=mock_gov)
    orch._redis = MagicMock()

    result = await orch.submit_goal("some goal")
    assert result["status"] == "rejected"


@pytest.mark.asyncio
async def test_tick_calls_breach_check_and_auto_retire():
    orch = _make_orchestrator()
    orch._governor.check_breach = AsyncMock(
        return_value=MagicMock(breached=False, reasons=[])
    )
    orch._governor.auto_retire_idle = AsyncMock(return_value=[])

    result = await orch.tick()

    orch._governor.check_breach.assert_called_once()
    orch._governor.auto_retire_idle.assert_called_once()
    assert "tick_ts" in result


@pytest.mark.asyncio
async def test_trigger_debate_emits_events():
    orch = _make_orchestrator()
    orch._debate = AsyncMock()
    debate_result = MagicMock(consensus="Both claims are partially valid", confidence=0.7)
    orch._debate.run = AsyncMock(return_value=debate_result)
    orch._blackboard.post = AsyncMock()

    from app.tenancy.context import PlanTier, TenantContext

    orch._tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")

    result = await orch.trigger_debate(
        topic="system_performance",
        claim_a={"content": "system is slow"},
        claim_b={"content": "system is fast"},
        initiator_agent_id="a1",
    )

    assert result["debate_id"]
    assert result["topic"] == "system_performance"
    orch._bus.publish.assert_called()


@pytest.mark.asyncio
async def test_submit_goal_needs_new_agent_spawns():
    """When society has no active members, orchestrator triggers spawn."""
    mock_society = AsyncMock()
    mock_society.route_goal = AsyncMock(
        return_value={
            "mode": "needs_new_agent",
            "agent_id": None,
            "confidence": 0.0,
            "reason": "no active members",
        }
    )
    mock_society.load_members = AsyncMock(return_value=[])
    mock_society.get_metrics = AsyncMock(return_value={})
    mock_society.get_lineage_graph = AsyncMock(return_value={"nodes": [], "edges": []})

    from app.civilization.models import SpawnDecision, SpawnVerdict

    mock_gov = AsyncMock()
    mock_gov.is_paused_sync = MagicMock(return_value=False)
    verdict = SpawnVerdict(
        decision=SpawnDecision.APPROVED,
        reason="within limits",
        allowed_budget_usd=5.0,
        clamped_autonomy="bounded-autonomous",
        inherited_policy_ids=[],
        snapshot={},
    )
    mock_gov.evaluate_spawn_request = AsyncMock(return_value=verdict)
    mock_gov.spawn_agent = AsyncMock(return_value={"agent_id": "new-agent-1"})

    orch = _make_orchestrator(governor=mock_gov, society=mock_society)

    from app.tenancy.context import PlanTier, TenantContext

    orch._tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")

    result = await orch.submit_goal("Build a data pipeline")

    mock_gov.evaluate_spawn_request.assert_called_once()
    mock_gov.spawn_agent.assert_called_once()
    assert result["status"] == "accepted"


@pytest.mark.asyncio
async def test_submit_goal_spawn_denied_returns_rejected():
    """Governor denying spawn → rejected response."""
    mock_society = AsyncMock()
    mock_society.route_goal = AsyncMock(
        return_value={
            "mode": "needs_new_agent",
            "agent_id": None,
            "confidence": 0.0,
            "reason": "no members",
        }
    )

    from app.civilization.models import SpawnDecision, SpawnVerdict

    mock_gov = AsyncMock()
    mock_gov.is_paused_sync = MagicMock(return_value=False)
    denied_verdict = SpawnVerdict(
        decision=SpawnDecision.DENIED,
        reason="budget exceeded",
        allowed_budget_usd=0.0,
    )
    mock_gov.evaluate_spawn_request = AsyncMock(return_value=denied_verdict)

    orch = _make_orchestrator(governor=mock_gov, society=mock_society)

    from app.tenancy.context import PlanTier, TenantContext

    orch._tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")

    result = await orch.submit_goal("Do something expensive")
    assert result["status"] == "rejected"
    assert "budget" in result["reason"]


@pytest.mark.asyncio
async def test_tick_emits_retired_events():
    """Tick emits AGENT_RETIRED events for each auto-retired agent."""
    orch = _make_orchestrator()
    orch._governor.check_breach = AsyncMock(
        return_value=MagicMock(breached=False, reasons=[])
    )
    orch._governor.auto_retire_idle = AsyncMock(return_value=["agent-old-1", "agent-old-2"])

    result = await orch.tick()
    assert result["auto_retired"] == ["agent-old-1", "agent-old-2"]


@pytest.mark.asyncio
async def test_get_status_returns_full_snapshot():
    orch = _make_orchestrator()
    orch._society.get_metrics = AsyncMock(
        return_value={"total_members": 3, "active_members": 2}
    )
    orch._society.get_lineage_graph = AsyncMock(
        return_value={"nodes": [{"id": "a1"}], "edges": [], "member_count": 1}
    )

    status = await orch.get_status()
    assert status["civilization_id"] == "civ-1"
    assert status["society"]["total_members"] == 3
    assert "constitution" in status
    assert "lineage" in status


@pytest.mark.asyncio
async def test_trigger_debate_without_debate_orchestrator():
    """trigger_debate still returns a result dict even with no DebateOrchestrator."""
    orch = _make_orchestrator()
    orch._debate = None  # no DebateOrchestrator wired

    from app.tenancy.context import PlanTier, TenantContext

    orch._tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")

    result = await orch.trigger_debate(
        topic="memory_strategy",
        claim_a={"content": "use vector db"},
        claim_b={"content": "use graph db"},
        initiator_agent_id="a1",
    )

    assert result["topic"] == "memory_strategy"
    assert result["status"] == "concluded"
    assert result["consensus"] is None
    orch._bus.publish.assert_called_once()
