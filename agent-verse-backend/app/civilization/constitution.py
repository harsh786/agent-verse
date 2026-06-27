"""Constitution — pure policy evaluator. Zero I/O. Fully unit-testable."""
from __future__ import annotations

from app.civilization.models import (
    BreachContext,
    BreachVerdict,
    Constitution,
    SpawnContext,
    SpawnDecision,
    SpawnVerdict,
)


def evaluate_spawn(ctx: SpawnContext, constitution: Constitution) -> SpawnVerdict:
    """Evaluate whether a spawn request satisfies the Constitution.

    Returns SpawnVerdict with APPROVED or DENIED + snapshot of enforcement state.
    """
    reasons = []

    # Depth check
    if ctx.depth >= constitution.max_depth:
        reasons.append(
            f"depth {ctx.depth} >= max_depth {constitution.max_depth}"
        )

    # Total agents check
    if ctx.current_total_agents >= constitution.max_total_agents:
        reasons.append(
            f"total_agents {ctx.current_total_agents} >= max_total_agents "
            f"{constitution.max_total_agents}"
        )

    # Concurrent agents check
    if ctx.current_concurrent_agents >= constitution.max_concurrent_agents:
        reasons.append(
            f"concurrent_agents {ctx.current_concurrent_agents} >= "
            f"max_concurrent_agents {constitution.max_concurrent_agents}"
        )

    # Spawn rate check
    if ctx.spawn_rate_last_min >= constitution.spawn_rate_limit_per_min:
        reasons.append(
            f"spawn_rate {ctx.spawn_rate_last_min}/min >= "
            f"limit {constitution.spawn_rate_limit_per_min}/min"
        )

    # Budget check
    child_budget = constitution.compute_child_budget(ctx.parent_budget_usd, ctx.depth)
    remaining = constitution.total_budget_usd - ctx.civilization_budget_spent_usd
    if child_budget > remaining:
        reasons.append(
            f"child_budget ${child_budget:.4f} > remaining ${remaining:.4f}"
        )

    snapshot = {
        "depth": ctx.depth,
        "total_agents": ctx.current_total_agents,
        "concurrent_agents": ctx.current_concurrent_agents,
        "spawn_rate_last_min": ctx.spawn_rate_last_min,
        "civilization_budget_spent_usd": ctx.civilization_budget_spent_usd,
        "child_budget_computed": child_budget,
        "max_depth": constitution.max_depth,
        "max_total_agents": constitution.max_total_agents,
        "max_concurrent_agents": constitution.max_concurrent_agents,
        "total_budget_usd": constitution.total_budget_usd,
    }

    if reasons:
        return SpawnVerdict(
            decision=SpawnDecision.DENIED,
            reason="; ".join(reasons),
            allowed_budget_usd=0.0,
            snapshot=snapshot,
        )

    # Clamp autonomy to ceiling
    autonomy_order = ["supervised", "bounded-autonomous", "fully-autonomous"]
    ceiling_idx = (
        autonomy_order.index(constitution.autonomy_ceiling)
        if constitution.autonomy_ceiling in autonomy_order
        else 1
    )
    requested_idx = 1  # default to bounded-autonomous for spawned agents
    clamped_autonomy = autonomy_order[min(requested_idx, ceiling_idx)]

    # Inherit civilization policies
    inherited = list(set(constitution.inherited_policy_ids + ctx.parent_policy_ids))

    return SpawnVerdict(
        decision=SpawnDecision.APPROVED,
        reason="spawn approved within Constitution bounds",
        allowed_budget_usd=child_budget,
        clamped_autonomy=clamped_autonomy,
        inherited_policy_ids=inherited,
        snapshot=snapshot,
    )


def evaluate_breach(ctx: BreachContext, constitution: Constitution) -> BreachVerdict:
    """Check if the civilization is in a Constitutional breach state."""
    reasons = []

    if ctx.budget_spent_usd >= ctx.budget_total_usd:
        reasons.append(
            f"budget exhausted: ${ctx.budget_spent_usd:.2f} >= ${ctx.budget_total_usd:.2f}"
        )

    if ctx.spawn_rate_last_min >= constitution.spawn_rate_limit_per_min:
        reasons.append(
            f"spawn rate {ctx.spawn_rate_last_min}/min >= "
            f"limit {constitution.spawn_rate_limit_per_min}/min"
        )

    if ctx.total_agents > constitution.max_total_agents:
        reasons.append(
            f"total agents {ctx.total_agents} > max {constitution.max_total_agents}"
        )

    return BreachVerdict(breached=bool(reasons), reasons=reasons)
