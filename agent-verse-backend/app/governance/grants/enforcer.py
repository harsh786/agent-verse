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
        # No grant subsystem wired → do not block (governance is additive).
        return GrantDecision(True, "grants_not_configured")

    grants = await store.list_for_agent(tenant_id, agent_id)
    if not grants:
        if require_grant:
            return GrantDecision(False, "no_grant_for_agent")
        return GrantDecision(True, "no_grants_issued")

    active = [g for g in grants if g.is_active(_now)]
    if not active:
        return GrantDecision(False, "all_grants_expired_or_revoked")

    for grant in active:
        if grant.covers(tool_name, _now, cost_usd=cost_usd):
            return GrantDecision(True, "covered", grant_id=grant.grant_id)

    # A grant exists and is active but none covers this tool/cost.
    over_cost = any(
        g.max_cost_usd is not None and cost_usd > g.max_cost_usd for g in active
    )
    reason = "cost_cap_exceeded" if over_cost else "tool_out_of_scope"
    return GrantDecision(False, reason)


__all__ = ["GrantDecision", "check_grant"]
