"""Charge a non-executor LLM call (planner / verifier / reasoning) through the cost path.

Only the executor's LLM calls used to reach the budget (``cost_controller``), the
token ledger (``cost_tracker``) and grant spend; planner, verifier and reasoning
calls logged ``cost=0.0`` and were never charged, so tenant budgets saw a fraction
of real spend. Every such call now goes through :func:`charge_llm_call`, which
mirrors the executor's cost path (steps 1 / 1b / 1c / 2.3 in ``_execute_step``).
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


def llm_call_tokens(resp: Any) -> tuple[int, int]:
    """(prompt, completion) tokens — provider ``usage`` when present, else the totals."""
    usage = getattr(resp, "usage", None)
    if usage is not None:
        return int(getattr(usage, "prompt_tokens", 0) or 0), int(
            getattr(usage, "completion_tokens", 0) or 0
        )
    return int(getattr(resp, "input_tokens", 0) or 0), int(getattr(resp, "output_tokens", 0) or 0)


def charge_goal_id(agent_state: Any) -> str:
    """The goal id an LLM call made for ``agent_state`` is charged (and ledgered) to.

    A goal-tree sub-agent runs under its own child id but spends its parent's
    budget: charge the parent goal, never an unrelated id.
    """
    context = getattr(agent_state, "context", None)
    if isinstance(context, dict) and context.get("_budget_goal_id"):
        return str(context["_budget_goal_id"])
    return str(getattr(agent_state, "goal_id", "") or "")


def can_reserve_llm_spend(cost_controller: Any) -> bool:
    """Whether ``cost_controller`` can hold a reservation (charge now, give back later)."""
    return callable(getattr(cost_controller, "check_and_record", None)) and callable(
        getattr(cost_controller, "refund_async", None)
    )


async def reserve_llm_spend(
    cost_controller: Any, *, goal_id: str, tenant_ctx: Any, amount: float
) -> bool:
    """Reserve ``amount`` USD of budget BEFORE an LLM call (atomic check-and-record).

    Concurrent calls each passing a "budget left?" preflight before any of them
    is charged could together overshoot the budget; a reservation counts against
    the budget at once, so the next call sees it. Settle it with
    :func:`charge_llm_call` (``reserved_usd=``) or give it back with
    :func:`refund_llm_spend` when the call failed. False = the budget cannot
    cover it (nothing was recorded).
    """
    if amount <= 0:
        return True
    from app.governance.cost import llm_spend

    return bool(
        await llm_spend(
            cost_controller.check_and_record(
                goal_id=goal_id, cost_usd=amount, tenant_ctx=tenant_ctx
            )
        )
    )


async def refund_llm_spend(
    cost_controller: Any, *, goal_id: str, tenant_ctx: Any, amount: float, reason: str
) -> None:
    """Give back ``amount`` USD of a reservation. Never raises (logged instead)."""
    if amount <= 0:
        return
    try:
        await cost_controller.refund_async(
            goal_id=goal_id, cost_usd=amount, tenant_ctx=tenant_ctx, reason=reason
        )
    except Exception as exc:
        logger.warning(
            "llm_cost_refund_failed",
            goal_id=goal_id,
            amount=amount,
            reason=reason,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )


async def charge_llm_call(
    graph: Any,
    *,
    resp: Any,
    role: str,
    model: str,
    agent_state: Any,
    tenant_ctx: Any,
    reserved_usd: float = 0.0,
) -> float:
    """Charge one LLM call to the goal/tenant. Returns its USD cost.

    Never raises. When the cost controller denies the spend the goal is latched
    ``_budget_exhausted`` (the same latch the executor sets), so the routing layer
    hard-stops it with a budget reason.

    ``reserved_usd``: the budget already reserved for this call through
    :func:`reserve_llm_spend` on the same controller and goal. The budget step
    then only settles the difference (charges the excess, refunds the rest);
    the ledger, grant spend and role breakdown record the real cost as usual.
    """
    cost_controller = getattr(graph, "_cost_controller", None)
    goal_id = charge_goal_id(agent_state)

    async def _refund_reservation() -> float:
        if reserved_usd > 0 and cost_controller is not None and tenant_ctx is not None:
            await refund_llm_spend(
                cost_controller, goal_id=goal_id, tenant_ctx=tenant_ctx,
                amount=reserved_usd, reason=f"{role}:no_usage",
            )
        return 0.0

    try:
        from app.intelligence.cost_tracker import calculate_cost

        served_model = str(getattr(resp, "model", "") or model or "")
        prompt_tokens, completion_tokens = llm_call_tokens(resp)
        if prompt_tokens <= 0 and completion_tokens <= 0:
            return await _refund_reservation()
        cost = float(calculate_cost(served_model, prompt_tokens, completion_tokens))
    except Exception:
        return await _refund_reservation()

    context = getattr(agent_state, "context", None)
    lock = getattr(graph, "_state_lock", None)
    if isinstance(context, dict):
        if lock is not None:
            async with lock:
                context["total_cost_usd"] = float(context.get("total_cost_usd", 0.0)) + cost
        else:
            context["total_cost_usd"] = float(context.get("total_cost_usd", 0.0)) + cost

    # 1. Budget (per-goal + per-tenant-daily). Denial latches the goal.
    if cost_controller is not None and tenant_ctx is not None:
        try:
            from app.governance.cost import llm_spend

            # A reserved call only settles the difference against its reservation.
            due = cost - reserved_usd if reserved_usd > 0 else cost
            if due < 0:
                await refund_llm_spend(
                    cost_controller, goal_id=goal_id, tenant_ctx=tenant_ctx,
                    amount=-due, reason=f"{role}:settle",
                )
                ok = True
            elif due == 0 and reserved_usd > 0:
                ok = True
            else:
                ok = await llm_spend(
                    cost_controller.check_and_record(
                        goal_id=goal_id, cost_usd=due, tenant_ctx=tenant_ctx
                    )
                )
        except Exception as exc:
            # Fail closed: a controller outage must not crash planning/verification,
            # but it also must not let spend through unmetered (this used to set
            # ok=True). Latch the goal so routing stops it with an explicit reason.
            ok = False
            if isinstance(context, dict):
                context["_budget_check_error"] = f"{type(exc).__name__}: {exc}"[:200]
        if not ok and isinstance(context, dict):
            context["_budget_exhausted"] = True

    # 2. Token ledger (cost_ledger row + CostTracker's per-goal / per-tenant Redis
    # counters). A failure must not stop the goal, but it must be visible: it was
    # swallowed silently, which hid that worker goals wrote no ledger rows at all.
    cost_tracker = getattr(graph, "_cost_tracker", None)
    if cost_tracker is not None and tenant_ctx is not None:
        try:
            await cost_tracker.record_llm_usage(
                model=served_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                tenant_ctx=tenant_ctx,
                goal_id=goal_id,
                agent_id=context.get("agent_id") if isinstance(context, dict) else None,
                role=role,
            )
        except Exception as exc:
            logger.warning(
                "llm_cost_ledger_record_failed",
                role=role,
                model=served_model,
                goal_id=goal_id,
                tenant_id=getattr(tenant_ctx, "tenant_id", None),
                error=f"{type(exc).__name__}: {exc}"[:300],
            )

    # 3. Grant spend (Grant.max_cost_usd binds on real spend).
    grant_store = getattr(graph, "_grant_store", None)
    if getattr(graph, "_enforce_grants", False) and grant_store is not None and cost > 0.0:
        try:
            from datetime import UTC, datetime

            from app.governance.grants.enforcer import active_grants

            now = datetime.now(UTC)
            covering = await active_grants(
                grant_store, tenant_ctx.tenant_id, getattr(graph, "_agent_id", None) or "", now
            )
            for grant in covering:
                if grant.max_cost_usd is not None:
                    await grant_store.record_spend(tenant_ctx.tenant_id, grant.grant_id, cost)
                    break
        except Exception as exc:
            logger.warning(
                "llm_cost_grant_spend_failed",
                role=role,
                goal_id=goal_id,
                error=f"{type(exc).__name__}: {exc}"[:300],
            )

    # 4. Per-goal, per-role breakdown — with the REAL cost (was hard-coded 0.0).
    try:
        from app.observability.cost_breakdown import arecord_role_cost
        from app.providers.circuit_breaker import fallback_from_of

        # Provenance: ``served_model`` is the model that answered (after any
        # failover), ``fallback_from`` the models tried before it.
        await arecord_role_cost(
            goal_id=goal_id,
            tenant_id=getattr(tenant_ctx, "tenant_id", None),
            role=role,
            model=served_model,
            input_tok=prompt_tokens,
            output_tok=completion_tokens,
            cost=cost,
            fallback_from=[m for m in fallback_from_of(resp) if m != served_model],
        )
    except Exception as exc:
        logger.warning(
            "llm_cost_role_breakdown_failed",
            role=role,
            goal_id=goal_id,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )
    return cost


class ChargingProvider:
    """Provider proxy that charges every ``complete`` call via :func:`charge_llm_call`.

    Used for reasoning patterns (self-consistency, tree-of-thoughts, peer review,
    supervisor decomposition) that call the provider internally, so their LLM spend
    reaches the tenant budget too. Everything else is delegated unchanged.
    Each call also gets the per-model circuit breaker and a bounded timeout.
    """

    # complete_decision() on this proxy calls it directly (no double charge).
    _agentverse_guarded = True

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
        from app.providers.guarded_completion import (
            complete_decision,
            generation_timeout_seconds,
        )

        # Breaker + timeout only; charged just below against this goal.
        resp = await complete_decision(
            self._inner,
            request,
            role=self._role,
            charge=False,
            timeout_seconds=generation_timeout_seconds(),
        )
        await charge_llm_call(
            self._graph,
            resp=resp,
            role=self._role,
            model=str(getattr(request, "model", "") or ""),
            agent_state=self._agent_state,
            tenant_ctx=self._tenant_ctx,
        )
        return resp
