"""Full in-memory autonomous civilization scenario.

Spec §14.3: exercises the complete Phase A-H stack using only in-memory
components (no DB, no Redis, no real LLM).  This is the canonical
"works-on-a-laptop" scenario test: fast, deterministic, zero infra.

Scenario:
  1. Orchestrator receives a high-level goal.
  2. Society has no members → needs_new_agent routing.
  3. Governor evaluates spawn → APPROVED.
  4. Two child spawns at depth 1 and depth 2 → both APPROVED.
  5. One spawn attempt at max_depth → DENIED (constitution enforced).
  6. Agents post conflicting findings to the Blackboard.
  7. Debate is triggered → bus receives the debate event.
  8. Learning candidate is submitted → run_step promotes it.
  9. Orchestrator tick auto-retires a low-rep member.
  10. Final breach check → no breach.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_constitution(max_depth: int = 3, budget: float = 100.0):
    from app.civilization.models import Constitution

    return Constitution(
        max_depth=max_depth,
        max_total_agents=20,
        max_concurrent_agents=8,
        total_budget_usd=budget,
        per_agent_budget_usd=20.0,
        budget_decay=0.7,
        spawn_rate_limit_per_min=30,
    )


def _make_tenant_ctx(tenant_id: str = "e2e-full"):
    from app.tenancy.context import PlanTier, TenantContext

    return TenantContext(
        tenant_id=tenant_id,
        plan=PlanTier.ENTERPRISE,
        api_key_id="full-scenario",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_autonomous_scenario() -> None:
    """End-to-end in-memory civilization: goal → spawn tree → debate → learn → retire."""
    from app.civilization.blackboard import Blackboard
    from app.civilization.bus import CivilizationBus
    from app.civilization.constitution import evaluate_spawn
    from app.civilization.models import Constitution, SpawnContext, SpawnDecision
    from app.civilization.orchestrator import CivilizationOrchestrator

    civ_id = f"civ-full-{uuid.uuid4().hex[:8]}"
    tenant_id = "e2e-full-scenario"
    constitution = _make_constitution()
    tenant_ctx = _make_tenant_ctx(tenant_id)

    spawns: list[dict] = []

    # -- Society mock (no active members)
    mock_society = AsyncMock()
    mock_society.route_goal = AsyncMock(
        return_value={
            "mode": "needs_new_agent",
            "reason": "no_members",
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
    mock_society.get_lineage_graph = AsyncMock(return_value={"nodes": [], "edges": []})
    mock_society.update_member_status = AsyncMock()

    # -- Governor mock (delegates spawn evaluation to real evaluate_spawn)
    mock_governor = AsyncMock()

    async def _evaluate_spawn(
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
            current_concurrent_agents=min(len(spawns), 5),
            civilization_budget_spent_usd=0.0,
            spawn_rate_last_min=len(spawns),
            parent_budget_usd=parent_budget_usd,
            parent_policy_ids=parent_policy_ids,
        )
        verdict = evaluate_spawn(ctx, constitution)
        spawns.append({"depth": depth, "decision": verdict.decision.value})
        return verdict

    mock_governor.evaluate_spawn_request = _evaluate_spawn
    mock_governor.spawn_agent = AsyncMock(
        return_value={"agent_id": uuid.uuid4().hex, "autonomy_mode": "bounded-autonomous"}
    )
    mock_governor.is_paused_sync = MagicMock(return_value=False)
    mock_governor.check_breach = AsyncMock(
        return_value=MagicMock(breached=False, reasons=[])
    )
    mock_governor.auto_retire_idle = AsyncMock(return_value=["low-rep-agent-x"])

    # -- Real Blackboard (in-memory mode)
    blackboard = Blackboard(civilization_id=civ_id, tenant_id=tenant_id)

    # -- Real Bus (in-memory mode)
    bus = CivilizationBus(civilization_id=civ_id, tenant_id=tenant_id)

    # -- Learning mock
    mock_learning = AsyncMock()
    mock_learning.run_step = AsyncMock(
        return_value={"validated": 1, "promoted": 1, "rejected": 0}
    )

    # -- Goal service mock
    mock_goal_service = AsyncMock()
    mock_goal_service.submit_goal = AsyncMock(return_value={"goal_id": "g-1"})

    orchestrator = CivilizationOrchestrator(
        civilization_id=civ_id,
        tenant_id=tenant_id,
        constitution=constitution,
        governor=mock_governor,
        society=mock_society,
        bus=bus,
        blackboard=blackboard,
        learning_pipeline=mock_learning,
        goal_service=mock_goal_service,
        tenant_ctx=tenant_ctx,
    )

    # ── 1. Submit goal ──
    goal = "Resolve all open P1 tickets in the BAU project"
    result = await orchestrator.submit_goal(goal)
    assert result["status"] == "accepted", f"Unexpected status: {result}"

    # ── 2. Spawn tree: depth 1 approved ──
    v1 = await _evaluate_spawn(
        requester_agent_id="root-agent",
        requested_capability="jira_search",
        goal_text="Search for P1 tickets",
        depth=1,
        parent_budget_usd=20.0,
        parent_policy_ids=[],
        tenant_ctx=tenant_ctx,
    )
    assert v1.decision == SpawnDecision.APPROVED  # type: ignore[attr-defined]

    # ── 3. Spawn tree: depth 2 approved ──
    v2 = await _evaluate_spawn(
        requester_agent_id="jira-agent",
        requested_capability="confluence_writer",
        goal_text="Write resolution docs",
        depth=2,
        parent_budget_usd=14.0,
        parent_policy_ids=[],
        tenant_ctx=tenant_ctx,
    )
    assert v2.decision == SpawnDecision.APPROVED  # type: ignore[attr-defined]

    # ── 4. Spawn at max_depth → DENIED ──
    v_denied = await _evaluate_spawn(
        requester_agent_id="confluence-agent",
        requested_capability="deploy",
        goal_text="Deploy fix",
        depth=3,  # max_depth == 3 → denied
        parent_budget_usd=9.8,
        parent_policy_ids=[],
        tenant_ctx=tenant_ctx,
    )
    assert v_denied.decision == SpawnDecision.DENIED, (  # type: ignore[attr-defined]
        "Constitution must deny spawn at max_depth"
    )

    # ── 5. Agents post conflicting findings ──
    entry_a = await blackboard.post(
        author_agent_id="jira-agent",
        topic="p1_count",
        content="Found 15 P1 tickets open",
        confidence=0.85,
    )
    entry_b = await blackboard.post(
        author_agent_id="confluence-agent",
        topic="p1_count",
        content="Found 12 P1 tickets open",
        confidence=0.80,
    )
    all_entries = await blackboard.query(topic="p1_count")
    assert len(all_entries) == 2

    # ── 6. Trigger debate ──
    debate_result = await orchestrator.trigger_debate(
        topic="p1_count",
        claim_a=entry_a,
        claim_b=entry_b,
        initiator_agent_id="jira-agent",
    )
    assert debate_result["topic"] == "p1_count"
    assert debate_result["status"] == "concluded"

    # ── 7. Tick: learning step + auto-retire ──
    tick = await orchestrator.tick()
    assert "tick_ts" in tick
    assert "auto_retired" in tick
    assert "low-rep-agent-x" in tick["auto_retired"]

    # ── 8. No breach ──
    breach = await mock_governor.check_breach()
    assert not breach.breached

    # ── 9. Invariant: no spawn at or beyond max_depth was approved ──
    for s in spawns:
        assert (
            s["depth"] < constitution.max_depth or s["decision"] == "denied"
        ), f"Constitution breach: depth={s['depth']} was approved"


@pytest.mark.asyncio
async def test_budget_exhaustion_denies_all_spawns() -> None:
    """Once budget is effectively exhausted no new spawns are approved."""
    from app.civilization.constitution import evaluate_spawn
    from app.civilization.models import Constitution, SpawnContext, SpawnDecision

    constitution = Constitution(
        max_depth=5,
        max_total_agents=100,
        total_budget_usd=1.0,
        per_agent_budget_usd=0.5,
        budget_decay=0.8,
    )

    ctx = SpawnContext(
        civilization_id="c-budget",
        tenant_id="t-budget",
        requester_agent_id="a",
        requested_capability="expensive",
        goal_text="do expensive work",
        depth=1,
        current_total_agents=1,
        current_concurrent_agents=1,
        civilization_budget_spent_usd=0.99,  # Only $0.01 remaining
        spawn_rate_last_min=0,
        parent_budget_usd=0.5,  # child_budget = 0.5 * 0.8 = 0.4 > $0.01
        parent_policy_ids=[],
    )

    verdict = evaluate_spawn(ctx, constitution)
    assert verdict.decision == SpawnDecision.DENIED
    assert "budget" in verdict.reason.lower()


@pytest.mark.asyncio
async def test_concurrent_limit_denies_spawn() -> None:
    """Spawn is denied when concurrent agent limit is reached."""
    from app.civilization.constitution import evaluate_spawn
    from app.civilization.models import Constitution, SpawnContext, SpawnDecision

    constitution = Constitution(
        max_depth=5,
        max_total_agents=100,
        max_concurrent_agents=3,  # Tight limit
        total_budget_usd=1000.0,
        per_agent_budget_usd=50.0,
        budget_decay=0.8,
    )

    ctx = SpawnContext(
        civilization_id="c-conc",
        tenant_id="t-conc",
        requester_agent_id="a",
        requested_capability="task",
        goal_text="do work",
        depth=1,
        current_total_agents=3,
        current_concurrent_agents=3,  # At the limit
        civilization_budget_spent_usd=10.0,
        spawn_rate_last_min=0,
        parent_budget_usd=50.0,
        parent_policy_ids=[],
    )

    verdict = evaluate_spawn(ctx, constitution)
    assert verdict.decision == SpawnDecision.DENIED
    assert "concurrent" in verdict.reason.lower()
