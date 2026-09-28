"""Charge a non-executor LLM call (planner / verifier / reasoning) through the cost path.

Only the executor's LLM calls used to reach the budget (``cost_controller``), the
token ledger (``cost_tracker``) and grant spend; planner, verifier and reasoning
calls logged ``cost=0.0`` and were never charged, so tenant budgets saw a fraction
of real spend. Every such call now goes through :func:`charge_llm_call`, which
mirrors the executor's cost path (steps 1 / 1b / 1c / 2.3 in ``_execute_step``).
"""

from __future__ import annotations

import contextlib
from typing import Any


def llm_call_tokens(resp: Any) -> tuple[int, int]:
    """(prompt, completion) tokens — provider ``usage`` when present, else the totals."""
    usage = getattr(resp, "usage", None)
    if usage is not None:
        return int(getattr(usage, "prompt_tokens", 0) or 0), int(
            getattr(usage, "completion_tokens", 0) or 0
        )
    return int(getattr(resp, "input_tokens", 0) or 0), int(getattr(resp, "output_tokens", 0) or 0)


async def charge_llm_call(
    graph: Any,
    *,
    resp: Any,
    role: str,
    model: str,
    agent_state: Any,
    tenant_ctx: Any,
) -> float:
    """Charge one LLM call to the goal/tenant. Returns its USD cost.

    Never raises. When the cost controller denies the spend the goal is latched
    ``_budget_exhausted`` (the same latch the executor sets), so the routing layer
    hard-stops it with a budget reason.
    """
    try:
        from app.intelligence.cost_tracker import calculate_cost

        served_model = str(getattr(resp, "model", "") or model or "")
        prompt_tokens, completion_tokens = llm_call_tokens(resp)
        if prompt_tokens <= 0 and completion_tokens <= 0:
            return 0.0
        cost = float(calculate_cost(served_model, prompt_tokens, completion_tokens))
    except Exception:
        return 0.0

    context = getattr(agent_state, "context", None)
    goal_id = str(getattr(agent_state, "goal_id", "") or "")
    lock = getattr(graph, "_state_lock", None)
    if isinstance(context, dict):
        if lock is not None:
            async with lock:
                context["total_cost_usd"] = float(context.get("total_cost_usd", 0.0)) + cost
        else:
            context["total_cost_usd"] = float(context.get("total_cost_usd", 0.0)) + cost

    # 1. Budget (per-goal + per-tenant-daily). Denial latches the goal.
    cost_controller = getattr(graph, "_cost_controller", None)
    if cost_controller is not None and tenant_ctx is not None:
        try:
            ok = await cost_controller.check_and_record(
                goal_id=goal_id, cost_usd=cost, tenant_ctx=tenant_ctx
            )
        except Exception:
            ok = True  # a controller outage must not crash planning/verification
        if not ok and isinstance(context, dict):
            context["_budget_exhausted"] = True

    # 2. Token ledger.
    cost_tracker = getattr(graph, "_cost_tracker", None)
    if cost_tracker is not None and tenant_ctx is not None:
        with contextlib.suppress(Exception):
            await cost_tracker.record_llm_usage(
                model=served_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                tenant_ctx=tenant_ctx,
                goal_id=goal_id,
                agent_id=context.get("agent_id") if isinstance(context, dict) else None,
                role=role,
            )

    # 3. Grant spend (Grant.max_cost_usd binds on real spend).
    grant_store = getattr(graph, "_grant_store", None)
    if getattr(graph, "_enforce_grants", False) and grant_store is not None and cost > 0.0:
        with contextlib.suppress(Exception):
            from datetime import UTC, datetime

            covering = await grant_store.list_for_agent(
                tenant_ctx.tenant_id, getattr(graph, "_agent_id", None) or ""
            )
            now = datetime.now(UTC)
            for grant in covering:
                if grant.is_active(now) and grant.max_cost_usd is not None:
                    await grant_store.record_spend(tenant_ctx.tenant_id, grant.grant_id, cost)
                    break

    # 4. Per-goal, per-role breakdown — with the REAL cost (was hard-coded 0.0).
    with contextlib.suppress(Exception):
        from app.observability.cost_breakdown import arecord_role_cost

        await arecord_role_cost(
            goal_id=goal_id,
            tenant_id=getattr(tenant_ctx, "tenant_id", None),
            role=role,
            model=served_model,
            input_tok=prompt_tokens,
            output_tok=completion_tokens,
            cost=cost,
        )
    return cost


class ChargingProvider:
    """Provider proxy that charges every ``complete`` call via :func:`charge_llm_call`.

    Used for reasoning patterns (self-consistency, tree-of-thoughts, peer review,
    supervisor decomposition) that call the provider internally, so their LLM spend
    reaches the tenant budget too. Everything else is delegated unchanged.
    """

    def __init__(
        self, provider: Any, *, graph: Any, role: str, agent_state: Any, tenant_ctx: Any
    ) -> None:
        self._inner = provider
        self._graph = graph
        self._role = role
        self._agent_state = agent_state
        self._tenant_ctx = tenant_ctx

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def complete(self, request: Any) -> Any:
        resp = await self._inner.complete(request)
        await charge_llm_call(
            self._graph,
            resp=resp,
            role=self._role,
            model=str(getattr(request, "model", "") or ""),
            agent_state=self._agent_state,
            tenant_ctx=self._tenant_ctx,
        )
        return resp
