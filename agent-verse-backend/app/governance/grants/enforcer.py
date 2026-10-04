"""Grant enforcement — the allow/deny decision a tool call must pass.

Composes with (does not replace) tool-risk, policy, and HITL gates: a call is
permitted only if a covering, active, unexpired, unrevoked grant exists for
(agent, tool) within its cost cap. Fail-closed: no covering grant → deny.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class GrantDecision:
    allowed: bool
    reason: str
    grant_id: str | None = None


async def active_grants(
    store: Any, tenant_id: str, agent_id: str, now: datetime
) -> tuple[Any, ...]:
    """The agent's currently active grants — the store's bounded lookup when it
    has one, else its full list filtered here."""
    getter = getattr(store, "active_for_agent", None)
    if getter is not None:
        return tuple(await getter(tenant_id, agent_id, now=now))
    grants = await store.list_for_agent(tenant_id, agent_id)
    return tuple(g for g in grants if g.is_active(now))


async def _ancestor_exhausted(store: Any, tenant_id: str, grant_id: str, cost: float) -> bool:
    probe = getattr(store, "ancestor_budget_exhausted", None)
    if probe is None:
        return False  # a store without delegation chains
    return bool(await probe(tenant_id, grant_id, cost))


async def _has_any_grant(store: Any, tenant_id: str, agent_id: str) -> bool:
    probe = getattr(store, "has_any_for_agent", None)
    if probe is not None:
        return bool(await probe(tenant_id, agent_id))
    return bool(await store.list_for_agent(tenant_id, agent_id))


async def check_grant(
    store: Any,
    *,
    tenant_id: str,
    agent_id: str,
    tool_name: str,
    now: datetime | None = None,
    cost_usd: float = 0.0,
    require_grant: bool = True,
) -> GrantDecision:
    """Decide whether ``agent_id`` may call ``tool_name`` under its grants.

    ``require_grant=False`` (default OFF stays opt-in per deployment) allows calls
    when no grants exist at all, easing rollout; once a tenant issues any grant,
    enforcement is strict for that agent.
    """
    _now = now or datetime.now(UTC)
    if store is None:
        # Enforcement ON but no grant store wired: fail CLOSED. It used to allow
        # every call here, so enforcement silently disappeared whenever the
        # store failed to wire (e.g. a worker without its DB). With enforcement
        # off (require_grant=False) governance stays additive.
        if require_grant:
            return GrantDecision(False, "grant_store_unavailable")
        return GrantDecision(True, "grants_not_configured")

    # Hot path (every tool call): only the agent's usable grants, filtered and
    # bounded by the store (GRANT-08: it loaded every grant ever issued).
    active = list(await active_grants(store, tenant_id, agent_id, _now))
    if not active:
        if not await _has_any_grant(store, tenant_id, agent_id):
            if require_grant:
                return GrantDecision(False, "no_grant_for_agent")
            return GrantDecision(True, "no_grants_issued")
        return GrantDecision(False, "all_grants_expired_or_revoked")

    ancestor_exhausted = False
    for grant in active:
        if grant.covers(tool_name, _now, cost_usd=cost_usd):
            # A delegated grant spends its ancestors' budget too: it covers
            # nothing once any ancestor's budget is consumed.
            if grant.parent_grant_id and await _ancestor_exhausted(
                store, tenant_id, grant.grant_id, cost_usd
            ):
                ancestor_exhausted = True
                continue
            return GrantDecision(True, "covered", grant_id=grant.grant_id)

    # A grant exists and is active but none covers this tool/cost.
    over_cost = ancestor_exhausted or any(g.budget_exhausted(cost_usd) for g in active)
    reason = "cost_cap_exceeded" if over_cost else "tool_out_of_scope"
    return GrantDecision(False, reason)


async def enforce_tool_call(
    store: Any,
    *,
    tenant_id: str,
    agent_id: str,
    tool_name: str,
    enabled: bool,
    cost_usd: float = 0.0,
    now: datetime | None = None,
) -> GrantDecision:
    """Framework entry point: the single call every tool action routes through.

    When ``enabled`` is False (default deployment posture until a tenant opts in),
    this is a pass-through so nothing regresses. When enabled, it applies
    :func:`check_grant` (fail-closed) so an agent may only perform an action it
    holds a covering, active, unrevoked grant for — making governance mandatory at
    the execution boundary rather than advisory.
    """
    if not enabled:
        return GrantDecision(True, "enforcement_disabled")
    return await check_grant(
        store,
        tenant_id=tenant_id,
        agent_id=agent_id,
        tool_name=tool_name,
        now=now,
        cost_usd=cost_usd,
        require_grant=True,
    )


__all__ = ["GrantDecision", "active_grants", "check_grant", "enforce_tool_call"]
