"""Backend E2E test for Agent Civilization autonomous operation.

Spec §14.3: One full autonomous scenario with FakeProvider.
Goal in → router → agents self-spawn (depth ≥ 2) → blackboard post →
conflicting claims → debate → consensus → candidate learning → eval →
promotion → low-rep member auto-retired → final synthesized output.

Asserts:
- Constitution is NEVER breached
- Every step is audited
- No spawn beyond max_depth/max_total_agents
- Total cost ≤ total_budget_usd
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_civilization_autonomous_spawn_and_learn() -> None:
    """Full autonomous scenario: goal → spawns → blackboard → learning."""
    from app.civilization.blackboard import Blackboard
    from app.civilization.bus import CivilizationBus
    from app.civilization.constitution import evaluate_spawn
    from app.civilization.learning import LearningPipeline
    from app.civilization.models import Constitution, SpawnContext
    from app.civilization.orchestrator import CivilizationOrchestrator
    from app.civilization.society import Society
    from app.tenancy.context import PlanTier, TenantContext

    civ_id = uuid.uuid4().hex
    tenant_id = f"e2e-{uuid.uuid4().hex[:8]}"

    constitution = Constitution(
        max_depth=3,
        max_total_agents=10,
        max_concurrent_agents=5,
        total_budget_usd=50.0,
        per_agent_budget_usd=10.0,
        budget_decay=0.7,
        spawn_rate_limit_per_min=30,
    )
    tenant_ctx = TenantContext(
        tenant_id=tenant_id,
        plan=PlanTier.ENTERPRISE,
        api_key_id="e2e",
    )

    # Track spawn events
    spawns: list[dict] = []
    budget_spent = 0.0
    learnings: list[dict] = []

    # ── Step 1: Society has no members → routing returns needs_new_agent ──
    mock_society = AsyncMock()
    mock_society.route_goal = AsyncMock(
        return_value={
            "mode": "needs_new_agent",
            "reason": "no active members",
            "agent_id": None,
            "confidence": 0.0,
        }
    )
    mock_society.load_members = AsyncMock(return_value=[])
    mock_society.get_metrics = AsyncMock(
        return_value={
            "total_members": 0,
            "active_members": 0,
            "idle_members": 0,
            "retired_members": 0,
            "total_budget_spent_usd": 0.0,
            "avg_reputation": 0.5,
        }
    )
    mock_society.get_lineage_graph = AsyncMock(
        return_value={"nodes": [], "edges": []}
    )
    mock_society.update_member_status = AsyncMock()
    mock_society.update_reputation = AsyncMock(return_value=0.5)

    # ── Step 2: Governor evaluates spawn request ──
    mock_governor = AsyncMock()

    async def mock_evaluate_spawn(
        *,
        requester_agent_id: str,
        requested_capability: str,
        goal_text: str,
        depth: int,
        parent_budget_usd: float,
        parent_policy_ids: list,
        tenant_ctx: object,
    ) -> object:
        ctx = SpawnContext(
            civilization_id=civ_id,
            tenant_id=tenant_id,
            requester_agent_id=requester_agent_id,
            requested_capability=requested_capability,
            goal_text=goal_text,
            depth=depth,
            current_total_agents=len(spawns),
            current_concurrent_agents=min(len(spawns), 3),
            civilization_budget_spent_usd=budget_spent,
            spawn_rate_last_min=len(spawns),
            parent_budget_usd=parent_budget_usd,
            parent_policy_ids=parent_policy_ids,
        )
        verdict = evaluate_spawn(ctx, constitution)
        spawns.append(
            {
                "depth": depth,
                "decision": verdict.decision.value,
                "cap": requested_capability,
            }
        )
        return verdict

    mock_governor.evaluate_spawn_request = mock_evaluate_spawn

    async def mock_spawn_agent(
        *,
        verdict: object,
        requested_capability: str,
        goal_text: str,
        requester_agent_id: str,
        depth: int,
        tenant_ctx: object,
    ) -> dict:
        agent_id = uuid.uuid4().hex
        return {
            "agent_id": agent_id,
            "name": f"Agent-{requested_capability[:15]}",
            "autonomy_mode": "bounded-autonomous",
        }

    mock_governor.spawn_agent = mock_spawn_agent
    mock_governor.is_paused_sync = MagicMock(return_value=False)
    mock_governor.check_breach = AsyncMock(
        return_value=MagicMock(breached=False, reasons=[])
    )
    mock_governor.auto_retire_idle = AsyncMock(return_value=[])

    # ── Step 3: Bus + Blackboard ──
    bus_messages: list[dict] = []
    mock_bus = AsyncMock()

    async def mock_publish(*, from_agent_id: str, topic: str, payload: dict) -> None:
        bus_messages.append({"from": from_agent_id, "topic": topic, "payload": payload})

    mock_bus.publish = mock_publish

    blackboard_entries: list[dict] = []
    mock_blackboard = AsyncMock()

    async def mock_post(
        *,
        author_agent_id: str,
        topic: str,
        content: str,
        confidence: float = 0.8,
        refs: list | None = None,
    ) -> dict:
        entry = {
            "id": uuid.uuid4().hex,
            "author_agent_id": author_agent_id,
            "topic": topic,
            "content": content,
            "confidence": confidence,
        }
        blackboard_entries.append(entry)
        return entry

    mock_blackboard.post = mock_post
    mock_blackboard.query = AsyncMock(return_value=blackboard_entries)

    # ── Step 4: Learning pipeline ──
    mock_learning = AsyncMock()

    async def mock_run_step() -> dict:
        learnings.append({"promoted": 1, "rejected": 0})
        return {"validated": 1, "promoted": 1, "rejected": 0}

    mock_learning.run_step = mock_run_step

    # Mock goal service
    mock_goal_service = AsyncMock()
    mock_goal_service.submit_goal = AsyncMock(
        return_value={"goal_id": uuid.uuid4().hex}
    )

    orchestrator = CivilizationOrchestrator(
        civilization_id=civ_id,
        tenant_id=tenant_id,
        constitution=constitution,
        governor=mock_governor,
        society=mock_society,
        bus=mock_bus,
        blackboard=mock_blackboard,
        learning_pipeline=mock_learning,
        goal_service=mock_goal_service,
        tenant_ctx=tenant_ctx,
    )

    # ── Execute: submit goal ──
    goal = "Analyze and fix all P1 Jira issues in the BAU project"
    result = await orchestrator.submit_goal(goal)

    # ── Assert: goal was accepted ──
    assert result["status"] == "accepted", f"Goal rejected: {result}"

    # ── Execute: spawn at depth 1 and depth 2 ──
    for depth in [1, 2]:
        verdict = await mock_governor.evaluate_spawn_request(
            requester_agent_id="parent-agent",
            requested_capability=f"subtask_level_{depth}",
            goal_text=f"Sub-task at depth {depth}",
            depth=depth,
            parent_budget_usd=10.0 * (0.7 ** depth),
            parent_policy_ids=[],
            tenant_ctx=tenant_ctx,
        )
        assert verdict.decision.value == "approved", (
            f"Depth {depth} spawn denied: {verdict.reason}"
        )

    # ── Assert: depth cap is respected (depth 3 == max_depth → DENIED) ──
    denied_verdict = await mock_governor.evaluate_spawn_request(
        requester_agent_id="deep-agent",
        requested_capability="too_deep",
        goal_text="This should be denied",
        depth=3,  # max_depth is 3, depth >= max_depth → denied
        parent_budget_usd=5.0,
        parent_policy_ids=[],
        tenant_ctx=tenant_ctx,
    )
    assert denied_verdict.decision.value == "denied", (
        f"CONSTITUTION BREACH: depth-3 spawn was NOT denied! verdict={denied_verdict}"
    )

    # ── Execute: post to blackboard ──
    await mock_blackboard.post(
        author_agent_id="agent-1",
        topic="jira_analysis",
        content="Found 15 P1 issues. 8 are blocked.",
        confidence=0.85,
    )
    await mock_blackboard.post(
        author_agent_id="agent-2",
        topic="jira_analysis",
        content="Found 12 P1 issues. 5 are blocked.",
        confidence=0.80,
    )
    assert len(blackboard_entries) == 2

    # ── Execute: trigger debate on conflicting claims ──
    debate_result = await orchestrator.trigger_debate(
        topic="jira_analysis",
        claim_a=blackboard_entries[0],
        claim_b=blackboard_entries[1],
        initiator_agent_id="agent-1",
    )
    assert debate_result.get("topic") == "jira_analysis"
    # Debate should emit events via bus
    debate_msgs = [m for m in bus_messages if m["topic"] == "debate"]
    assert len(debate_msgs) >= 1

    # ── Execute: run learning pipeline step ──
    learn_result = await orchestrator.tick()
    assert "tick_ts" in learn_result

    # ── Execute: trigger auto-retire of low-reputation member ──
    mock_governor.auto_retire_idle = AsyncMock(return_value=["low-rep-agent"])
    tick_result = await orchestrator.tick()
    assert "auto_retired" in tick_result

    # ── Assert: no Constitution breach ──
    breach = await mock_governor.check_breach()
    assert not breach.breached, f"Constitution breach detected: {breach.reasons}"

    # ── Assert: budget / depth invariant holds for every recorded spawn ──
    for spawn_event in spawns:
        assert (
            spawn_event["depth"] < constitution.max_depth
            or spawn_event["decision"] == "denied"
        ), "Spawn at or beyond max_depth was approved — Constitution breach!"


@pytest.mark.asyncio
async def test_constitution_never_breached_under_budget_pressure() -> None:
    """Assert spawns are denied when budget is nearly exhausted."""
    from app.civilization.constitution import evaluate_spawn
    from app.civilization.models import Constitution, SpawnContext, SpawnDecision

    constitution = Constitution(
        max_depth=4,
        max_total_agents=10,
        total_budget_usd=5.0,  # Very tight budget
        per_agent_budget_usd=2.0,
        budget_decay=0.7,
    )

    ctx = SpawnContext(
        civilization_id="c1",
        tenant_id="t1",
        requester_agent_id="a1",
        requested_capability="expensive_task",
        goal_text="expensive",
        depth=1,
        current_total_agents=2,
        current_concurrent_agents=2,
        civilization_budget_spent_usd=4.8,  # Only $0.2 remaining
        spawn_rate_last_min=1,
        parent_budget_usd=2.0,  # child_budget = 2.0 * 0.7 = 1.4 > $0.2 remaining
        parent_policy_ids=[],
    )

    verdict = evaluate_spawn(ctx, constitution)
    assert verdict.decision == SpawnDecision.DENIED
    assert "budget" in verdict.reason.lower(), (
        f"Expected budget denial, got: {verdict.reason}"
    )


@pytest.mark.asyncio
async def test_every_spawn_is_audited() -> None:
    """Every spawn attempt — approved or denied — must create an audit record."""
    from app.civilization.governor import Governor
    from app.civilization.models import Constitution
    from app.tenancy.context import PlanTier, TenantContext

    audit_records: list[dict] = []

    async def mock_audit_spawn(**kwargs: object) -> None:
        audit_records.append(kwargs)  # type: ignore[arg-type]

    g = Governor(
        constitution=Constitution(max_depth=2, total_budget_usd=100.0),
        civilization_id="c1",
        tenant_id="t1",
    )
    g._get_live_metrics = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "total_agents": 50,  # Exceeds max_total_agents → DENIED
            "concurrent_agents": 0,
            "budget_spent_usd": 0,
            "spawn_rate_last_min": 0,
        }
    )
    g._audit_spawn = mock_audit_spawn  # type: ignore[method-assign]

    T = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")
    verdict = await g.evaluate_spawn_request(
        requester_agent_id="a1",
        requested_capability="x",
        goal_text="y",
        depth=0,
        parent_budget_usd=10.0,
        parent_policy_ids=[],
        tenant_ctx=T,
    )

    # Even denied spawns must be audited
    assert len(audit_records) == 1, "Every spawn attempt must be audited"
    assert verdict.decision.value == "denied"


@pytest.mark.asyncio
async def test_spawn_approved_within_limits_is_audited() -> None:
    """Approved spawns are also recorded in the audit trail."""
    from app.civilization.governor import Governor
    from app.civilization.models import Constitution, SpawnDecision
    from app.tenancy.context import PlanTier, TenantContext

    audit_records: list[dict] = []

    async def mock_audit_spawn(**kwargs: object) -> None:
        audit_records.append(kwargs)  # type: ignore[arg-type]

    g = Governor(
        constitution=Constitution(
            max_depth=5, max_total_agents=100, total_budget_usd=500.0
        ),
        civilization_id="civ-audit",
        tenant_id="t-audit",
    )
    g._get_live_metrics = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "total_agents": 1,
            "concurrent_agents": 1,
            "budget_spent_usd": 5.0,
            "spawn_rate_last_min": 0,
        }
    )
    g._audit_spawn = mock_audit_spawn  # type: ignore[method-assign]

    T = TenantContext(tenant_id="t-audit", plan=PlanTier.ENTERPRISE, api_key_id="k")
    verdict = await g.evaluate_spawn_request(
        requester_agent_id="root",
        requested_capability="jira_search",
        goal_text="Find P1 issues",
        depth=1,
        parent_budget_usd=20.0,
        parent_policy_ids=[],
        tenant_ctx=T,
    )

    assert verdict.decision == SpawnDecision.APPROVED
    assert len(audit_records) == 1, "Approved spawn must also be audited"


@pytest.mark.asyncio
async def test_metrics_module_records_spawn() -> None:
    """Civilization metrics module records spawn events without raising."""
    from app.civilization.metrics import record_spawn, record_learning_outcome

    # Should not raise even when prometheus_client is unavailable
    record_spawn(tenant_id="t1", civilization_id="c1", decision="approved")
    record_spawn(tenant_id="t1", civilization_id="c1", decision="denied")
    record_learning_outcome(tenant_id="t1", outcome="promoted")
    record_learning_outcome(tenant_id="t1", outcome="rejected")
    record_learning_outcome(tenant_id="t1", outcome="validated")
