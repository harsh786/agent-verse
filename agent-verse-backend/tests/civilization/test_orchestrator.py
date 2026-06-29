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


# ── Additional coverage tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_goal_multi_agent_mode():
    """multi_agent mode dispatches to supervisor agent."""
    mock_society = AsyncMock()
    mock_society.route_goal = AsyncMock(return_value={
        "mode": "multi_agent",
        "agent_id": None,
        "confidence": 0.9,
        "reason": "multi-agent",
    })
    mock_society.load_members = AsyncMock(return_value=[])
    mock_society.get_metrics = AsyncMock(return_value={})
    mock_society.get_lineage_graph = AsyncMock(return_value={"nodes": [], "edges": []})

    mock_supervisor = AsyncMock()
    supervisor_result = MagicMock(synthesis="Final answer from supervisor agents")
    mock_supervisor.run = AsyncMock(return_value=supervisor_result)

    orch = _make_orchestrator(society=mock_society)
    orch._supervisor = mock_supervisor

    from app.tenancy.context import PlanTier, TenantContext
    orch._tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")

    result = await orch.submit_goal("Complex multi-step goal")
    assert result["status"] == "accepted"
    assert result["mode"] == "multi_agent"
    mock_supervisor.run.assert_called_once()


@pytest.mark.asyncio
async def test_submit_goal_multi_agent_supervisor_failure_falls_through():
    """If supervisor raises, falls through to single agent path."""
    mock_society = AsyncMock()
    mock_society.route_goal = AsyncMock(return_value={
        "mode": "multi_agent",
        "agent_id": "fallback-agent",
        "confidence": 0.7,
        "reason": "multi-agent",
    })
    mock_society.load_members = AsyncMock(return_value=[])
    mock_society.get_metrics = AsyncMock(return_value={})
    mock_society.get_lineage_graph = AsyncMock(return_value={"nodes": [], "edges": []})

    mock_supervisor = AsyncMock()
    mock_supervisor.run = AsyncMock(side_effect=RuntimeError("Supervisor failed"))

    orch = _make_orchestrator(society=mock_society)
    orch._supervisor = mock_supervisor

    from app.tenancy.context import PlanTier, TenantContext
    orch._tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")

    result = await orch.submit_goal("Complex goal (supervisor will fail)")
    # Falls through to single agent, so accepted
    assert result["status"] == "accepted"


@pytest.mark.asyncio
async def test_submit_goal_service_exception_is_handled():
    """GoalService submit failure is logged and orchestrator still returns accepted."""
    orch = _make_orchestrator()

    mock_gs = AsyncMock()
    mock_gs.submit_goal = AsyncMock(side_effect=RuntimeError("GoalService down"))
    orch._goal_service = mock_gs

    from app.tenancy.context import PlanTier, TenantContext
    orch._tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")

    result = await orch.submit_goal("Some goal")
    assert result["status"] == "accepted"
    assert result["goal_id"]  # still has a goal_id (the civ_* one)


@pytest.mark.asyncio
async def test_trigger_debate_handles_debate_run_exception():
    """Debate run exception is caught; result still returned with error key."""
    orch = _make_orchestrator()
    orch._debate = AsyncMock()
    orch._debate.run = AsyncMock(side_effect=RuntimeError("Debate engine failed"))
    orch._society.load_members = AsyncMock(return_value=[])

    from app.tenancy.context import PlanTier, TenantContext
    orch._tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")

    result = await orch.trigger_debate(
        topic="performance",
        claim_a={"content": "slow"},
        claim_b={"content": "fast"},
        initiator_agent_id="a1",
    )
    assert result["debate_id"]
    assert "error" in result
    orch._bus.publish.assert_called()


@pytest.mark.asyncio
async def test_trigger_debate_updates_and_restores_member_statuses():
    """trigger_debate sets members to 'debating' then restores to 'active'."""
    mock_society = AsyncMock()
    members = [
        {"agent_id": "a1", "reputation": 0.9, "status": "active"},
        {"agent_id": "a2", "reputation": 0.8, "status": "active"},
    ]
    mock_society.load_members = AsyncMock(return_value=members)
    mock_society.update_member_status = AsyncMock()
    mock_society.get_metrics = AsyncMock(return_value={})
    mock_society.get_lineage_graph = AsyncMock(return_value={"nodes": [], "edges": []})

    debate_result = MagicMock(consensus="agreed", confidence=0.8)
    mock_debate = AsyncMock()
    mock_debate.run = AsyncMock(return_value=debate_result)

    orch = _make_orchestrator(society=mock_society)
    orch._debate = mock_debate
    orch._blackboard.post = AsyncMock()

    from app.tenancy.context import PlanTier, TenantContext
    orch._tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")

    await orch.trigger_debate(
        topic="arch",
        claim_a={"content": "microservices"},
        claim_b={"content": "monolith"},
        initiator_agent_id="a1",
    )

    status_calls = mock_society.update_member_status.call_args_list
    debating_calls = [c for c in status_calls if "debating" in str(c)]
    active_calls = [c for c in status_calls if c.args[1] == "active" if len(c.args) > 1]
    assert len(debating_calls) > 0
    assert len(active_calls) > 0


@pytest.mark.asyncio
async def test_trigger_debate_posts_consensus_to_blackboard():
    orch = _make_orchestrator()
    orch._debate = AsyncMock()
    debate_result = MagicMock(consensus="Both partially valid", confidence=0.75)
    orch._debate.run = AsyncMock(return_value=debate_result)
    orch._blackboard.post = AsyncMock()
    orch._society.load_members = AsyncMock(return_value=[])

    from app.tenancy.context import PlanTier, TenantContext
    orch._tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")

    await orch.trigger_debate(
        topic="caching",
        claim_a={"content": "redis"},
        claim_b={"content": "memcached"},
        initiator_agent_id="a1",
    )
    orch._blackboard.post.assert_called_once()


@pytest.mark.asyncio
async def test_tick_with_no_governor():
    """Tick still returns result dict when governor is None."""
    orch = _make_orchestrator()
    orch._governor = None
    result = await orch.tick()
    assert "tick_ts" in result


@pytest.mark.asyncio
async def test_tick_breach_exception_is_swallowed():
    orch = _make_orchestrator()
    orch._governor.check_breach = AsyncMock(side_effect=RuntimeError("Breach check failed"))
    orch._governor.auto_retire_idle = AsyncMock(return_value=[])
    result = await orch.tick()
    assert "tick_ts" in result


@pytest.mark.asyncio
async def test_tick_auto_retire_exception_is_swallowed():
    orch = _make_orchestrator()
    orch._governor.check_breach = AsyncMock(
        return_value=MagicMock(breached=False, reasons=[])
    )
    orch._governor.auto_retire_idle = AsyncMock(side_effect=RuntimeError("Retire failed"))
    result = await orch.tick()
    assert "tick_ts" in result


@pytest.mark.asyncio
async def test_tick_learning_pipeline_runs():
    orch = _make_orchestrator()
    orch._governor.check_breach = AsyncMock(
        return_value=MagicMock(breached=False, reasons=[])
    )
    orch._governor.auto_retire_idle = AsyncMock(return_value=[])
    mock_learning = AsyncMock()
    mock_learning.run_step = AsyncMock(return_value={"validated": 2, "promoted": 1, "rejected": 0})
    orch._learning = mock_learning

    result = await orch.tick()
    assert result["learning"]["promoted"] == 1
    mock_learning.run_step.assert_called_once()


@pytest.mark.asyncio
async def test_tick_learning_exception_is_swallowed():
    orch = _make_orchestrator()
    orch._governor.check_breach = AsyncMock(
        return_value=MagicMock(breached=False, reasons=[])
    )
    orch._governor.auto_retire_idle = AsyncMock(return_value=[])
    mock_learning = AsyncMock()
    mock_learning.run_step = AsyncMock(side_effect=RuntimeError("Learning failed"))
    orch._learning = mock_learning

    result = await orch.tick()
    assert "tick_ts" in result
    assert "learning" not in result


@pytest.mark.asyncio
async def test_sync_reputation_from_evals_no_db():
    orch = _make_orchestrator()
    orch._db = None
    result = await orch.sync_reputation_from_evals()
    assert result == 0


@pytest.mark.asyncio
async def test_sync_reputation_from_evals_with_db():
    from types import SimpleNamespace

    class _noop_ctx:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None

    class _FakeRepSession:
        def __init__(self):
            self.executions = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def execute(self, stmt, params=None):
            self.executions.append(params)
            return SimpleNamespace(
                fetchall=lambda: [("agent-1", 0.85, 5)],
            )

    session = _FakeRepSession()
    orch = _make_orchestrator(db=lambda: session)
    orch._society.load_members = AsyncMock(return_value=[{"agent_id": "agent-1"}])
    orch._society.update_reputation = AsyncMock()

    result = await orch.sync_reputation_from_evals()
    assert result == 1
    orch._society.update_reputation.assert_called_once_with(
        agent_id="agent-1", new_score=0.85
    )


@pytest.mark.asyncio
async def test_sync_reputation_from_evals_skips_nonmembers():
    """Eval scores for agents not in society are skipped."""
    from types import SimpleNamespace

    class _FakeRepSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def execute(self, stmt, params=None):
            return SimpleNamespace(
                fetchall=lambda: [("agent-X", 0.85, 5)],  # not in society
            )

    session = _FakeRepSession()
    orch = _make_orchestrator(db=lambda: session)
    orch._society.load_members = AsyncMock(return_value=[{"agent_id": "agent-1"}])
    orch._society.update_reputation = AsyncMock()

    result = await orch.sync_reputation_from_evals()
    assert result == 0
    orch._society.update_reputation.assert_not_called()


@pytest.mark.asyncio
async def test_sync_reputation_from_evals_exception_returns_0():
    class _FailSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def execute(self, *_, **__):
            raise RuntimeError("DB failed")

    orch = _make_orchestrator(db=lambda: _FailSession())
    result = await orch.sync_reputation_from_evals()
    assert result == 0
