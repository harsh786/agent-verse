"""Narrow-decision LLM calls, charged and circuit-broken like the agent roles.

Guardrail judges, intent/agent routers, eval scorers and RAG graders used to call
``provider.complete(...)`` directly. That skipped both protections the planner
and verifier get (``verifier_mixin`` / ``planner_mixin``):

* **cost** — nothing reached the per-goal / per-tenant budget, the token ledger
  or the per-role breakdown, so these calls were free to the budget;
* **circuit breaker + timeout** — a hung provider could stall the call (and the
  request or goal waiting on it) indefinitely, and failures never opened the
  per-model circuit.

:func:`complete_decision` routes such a call through
:func:`app.providers.circuit_breaker.complete_with_failover` (per-model circuit,
bounded timeout) and charges it:

* inside a running goal (``goal_charge_scope``, entered by ``AgentGraph.run``) —
  through :func:`app.agent.nodes.llm_cost.charge_llm_call`, exactly like the
  planner/verifier: goal + tenant budget, ledger, grant spend, role breakdown,
  and a denial latches the goal's ``_budget_exhausted``;
* outside a goal (API guardrails, chat, routing before submission, evals) —
  against the tenant's daily budget and ledger via the platform cost services
  (``set_platform_cost_services``). Each call is charged under its own
  execution id, as the RAG cost guard does, so the per-goal cap cannot pool
  unrelated calls. A tenant already over budget is refused before the call, and
  a denied charge raises :class:`DecisionBudgetExceededError`, which callers
  treat like any other failure (their existing fail-closed / fallback path).
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from app.providers.circuit_breaker import complete_with_failover

_DEFAULT_TIMEOUT_S = 20.0
_log = logging.getLogger(__name__)

# Platform work that is deliberately not charged to any tenant. A decision call
# outside a goal, with no tenant and no system job, is refused once cost
# services are configured (PROV-05): it used to be silently free.
SYSTEM_JOBS: frozenset[str] = frozenset({"model_probe"})


class DecisionBudgetExceededError(RuntimeError):
    """The tenant (or goal) budget does not allow this LLM decision call."""

    # A budget refusal is not a provider failure: it must not open the circuit.
    provider_failure = False


@dataclass(frozen=True)
class _ChargeScope:
    graph: Any
    agent_state: Any
    tenant_ctx: Any


_scope: contextvars.ContextVar[_ChargeScope | None] = contextvars.ContextVar(
    "agentverse_decision_charge_scope", default=None
)
_platform_services: Callable[[], tuple[Any, Any]] | None = None
# The tenant an HTTP request (TenantMiddleware) or a tenant-serving background
# job acts for: decision calls made anywhere under it — deep inside library code
# that never sees a tenant — are budget-checked and charged to that tenant.
_tenant_scope: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "agentverse_decision_tenant_scope", default=None
)


def set_platform_cost_services(resolver: Callable[[], tuple[Any, Any]] | None) -> None:
    """Register ``() -> (cost_controller, cost_tracker)`` for calls outside a goal.

    A resolver (not the objects) so the lifespan's swap to the Redis-backed
    controller is picked up without re-registering.
    """
    global _platform_services
    _platform_services = resolver


@contextlib.contextmanager
def goal_charge_scope(graph: Any, agent_state: Any, tenant_ctx: Any) -> Iterator[None]:
    """Charge decision calls made while a goal runs to that goal (and its tenant)."""
    token = _scope.set(_ChargeScope(graph, agent_state, tenant_ctx))
    try:
        yield
    finally:
        _scope.reset(token)


@contextlib.contextmanager
def tenant_charge_scope(tenant_ctx: Any) -> Iterator[None]:
    """Charge decision calls without an explicit tenant to ``tenant_ctx``.

    Entered by ``TenantMiddleware`` for every authenticated request (and by
    background jobs serving one tenant). An explicit ``tenant_ctx`` /
    ``tenant_id`` argument and a running goal's scope both take precedence.
    """
    token = _tenant_scope.set(tenant_ctx)
    try:
        yield
    finally:
        _tenant_scope.reset(token)


_system_job: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agentverse_decision_system_job", default=None
)


@contextlib.contextmanager
def system_job_scope(name: str) -> Iterator[None]:
    """Run uncharged *platform* decision calls (``name`` must be in :data:`SYSTEM_JOBS`)."""
    if name not in SYSTEM_JOBS:
        raise ValueError(f"{name!r} is not an allowlisted system job")
    token = _system_job.set(name)
    try:
        yield
    finally:
        _system_job.reset(token)


def _timeout(explicit: float | None) -> float:
    if explicit is not None:
        return explicit
    try:
        return float(os.getenv("AGENTVERSE_DECISION_CALL_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_S))
    except ValueError:
        return _DEFAULT_TIMEOUT_S


def generation_timeout_seconds() -> float:
    """Timeout for a *generative* call routed through :func:`complete_decision`.

    The decision default (``AGENTVERSE_DECISION_CALL_TIMEOUT_SECONDS``, 20s) is
    sized for short classifier/judge replies. Summaries, workflow steps and
    answers use the general LLM call timeout the agent roles use
    (``AGENTVERSE_LLM_CALL_TIMEOUT_SECONDS``, 60s).
    """
    try:
        return float(os.getenv("AGENTVERSE_LLM_CALL_TIMEOUT_SECONDS", "60"))
    except ValueError:
        return 60.0


def _tenant(tenant_ctx: Any, tenant_id: str | None) -> Any:
    if tenant_ctx is not None:
        return tenant_ctx
    if tenant_id:
        return SimpleNamespace(tenant_id=str(tenant_id))
    return _tenant_scope.get()


def _platform() -> tuple[Any, Any]:
    """The configured cost services, ``(None, None)`` when none are registered.

    A resolver that fails is an unknown budget: fail closed (it used to return
    ``(None, None)`` so the charge was silently skipped).
    """
    if _platform_services is None:
        return None, None
    try:
        return _platform_services()
    except Exception as exc:
        _log.warning("decision_cost_services_unavailable: %s", str(exc)[:200])
        raise DecisionBudgetExceededError(f"cost services unavailable: {exc}") from exc


def _require_attribution(scope: _ChargeScope | None, tenant: Any) -> None:
    """Refuse a call nobody can be charged for (outside a goal, no tenant, no system job)."""
    if scope is not None or tenant is not None or _system_job.get() is not None:
        return
    controller, tracker = _platform()
    if controller is None and tracker is None:
        return  # no cost enforcement configured at all (bare library / unit-test use)
    raise DecisionBudgetExceededError(
        "no tenant to charge this LLM call to (run it in a goal, a tenant scope or a "
        "system job)"
    )


async def _preflight(scope: _ChargeScope | None, tenant: Any) -> None:
    if scope is not None:
        context = getattr(scope.agent_state, "context", None)
        if isinstance(context, dict) and context.get("_budget_exhausted"):
            raise DecisionBudgetExceededError("goal budget exhausted")
        return
    if tenant is None:
        return
    controller, _ = _platform()
    check = getattr(controller, "ahas_remaining_budget", None)
    if check is None:
        return  # the post-call charge still enforces the daily budget
    try:
        ok = await check(tenant_ctx=tenant)
    except Exception as exc:
        # Fail closed: an unknown budget must not let spend through.
        raise DecisionBudgetExceededError(f"budget check unavailable: {exc}") from exc
    if not ok:
        raise DecisionBudgetExceededError("tenant daily LLM budget exhausted")


async def _charge(
    scope: _ChargeScope | None,
    tenant: Any,
    *,
    resp: Any,
    role: str,
    model: str,
    goal_id: str | None,
) -> None:
    from app.agent.nodes.llm_cost import charge_llm_call

    if scope is not None:
        await charge_llm_call(
            scope.graph,
            resp=resp,
            role=role,
            model=model,
            agent_state=scope.agent_state,
            tenant_ctx=scope.tenant_ctx if scope.tenant_ctx is not None else tenant,
        )
        return
    if tenant is None:
        return
    controller, tracker = _platform()
    if controller is None and tracker is None:
        return
    shim = SimpleNamespace(_cost_controller=controller, _cost_tracker=tracker, _state_lock=None)
    state = SimpleNamespace(
        goal_id=goal_id or f"decision:{role}:{uuid.uuid4().hex}",
        context={},
    )
    await charge_llm_call(
        shim, resp=resp, role=role, model=model, agent_state=state, tenant_ctx=tenant
    )
    if state.context.get("_budget_exhausted"):
        raise DecisionBudgetExceededError("tenant LLM budget exhausted by this call")


async def preflight_decision(
    *, role: str, tenant_ctx: Any = None, tenant_id: str | None = None
) -> None:
    """Budget preflight for an LLM call that cannot go through :func:`complete_decision`.

    For streamed answers (chat): refuse BEFORE any token is generated when the
    tenant (or goal) is out of budget or the call cannot be attributed. Pair it
    with :func:`charge_streamed` after the stream.
    """
    del role
    scope = _scope.get()
    tenant = _tenant(tenant_ctx, tenant_id)
    _require_attribution(scope, tenant)
    await _preflight(scope, tenant)


async def charge_streamed(
    resp: Any,
    *,
    role: str,
    model: str = "",
    tenant_ctx: Any = None,
    tenant_id: str | None = None,
    goal_id: str | None = None,
) -> None:
    """Charge a streamed call's usage (``resp``: ``input_tokens`` / ``output_tokens`` /
    ``model``) to the goal / tenant budget and ledger, like :func:`complete_decision`.

    Raises :class:`DecisionBudgetExceededError` when this call exhausted the budget.
    """
    await _charge(
        _scope.get(),
        _tenant(tenant_ctx, tenant_id),
        resp=resp,
        role=role,
        model=model,
        goal_id=goal_id,
    )


class GuardedDecisionProvider:
    """Provider proxy whose every ``complete`` goes through :func:`complete_decision`.

    For components that take a provider and call it internally (the debate
    orchestrator and supervisor decomposition run inside the goal-submission
    HTTP request, before any goal exists): each call gets the circuit breaker,
    the bounded timeout, the tenant budget preflight and the post-call charge.
    Every other attribute is delegated to the wrapped provider.
    """

    # complete_decision() on this proxy calls it directly (no double charge).
    _agentverse_guarded = True

    def __init__(
        self,
        provider: Any,
        *,
        role: str,
        tenant_ctx: Any = None,
        tenant_id: str | None = None,
        goal_id: str | None = None,
    ) -> None:
        self._inner = provider
        self._role = role
        self._tenant_ctx = _tenant(tenant_ctx, tenant_id)
        self._goal_id = goal_id

    @property
    def inner(self) -> Any:
        return self._inner

    async def complete(self, request: Any) -> Any:
        return await complete_decision(
            self._inner,
            request,
            role=self._role,
            tenant_ctx=self._tenant_ctx,
            goal_id=self._goal_id,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


async def complete_decision(
    provider: Any,
    request: Any,
    *,
    role: str,
    tenant_ctx: Any = None,
    tenant_id: str | None = None,
    goal_id: str | None = None,
    timeout_seconds: float | None = None,
    charge: bool = True,
) -> Any:
    """``provider.complete(request)`` with circuit breaker, timeout and cost charging.

    ``charge=False`` is for providers already metered by an outer budget guard
    (the RAG strategy LLM is a ``_BudgetedProvider``), so a call is not charged
    twice; the circuit breaker and timeout still apply.

    A provider that is itself a guarded wrapper (class attribute
    ``_agentverse_guarded``: :class:`GuardedDecisionProvider`, the reasoning
    patterns' ``ChargingProvider``, the RAG ``_BudgetedProvider``) already applies
    the breaker, timeout and its own metering, so it is called directly — code
    that receives "a provider" can always route through here without charging a
    wrapped call twice.
    """
    if getattr(type(provider), "_agentverse_guarded", False):
        return await provider.complete(request)
    scope = _scope.get()
    tenant = _tenant(tenant_ctx, tenant_id)
    if charge:
        _require_attribution(scope, tenant)
        await _preflight(scope, tenant)
    import time

    from app.ai_router.health_feed import record_llm_outcome

    model = str(getattr(request, "model", "") or "")
    started = time.monotonic()
    try:
        resp = await complete_with_failover(
            provider, request, timeout_seconds=_timeout(timeout_seconds)
        )
    except Exception as exc:
        if getattr(exc, "provider_failure", True):
            record_llm_outcome(
                provider=provider, model=model, ok=False,
                latency_ms=(time.monotonic() - started) * 1000, error=str(exc),
            )
        raise
    record_llm_outcome(
        provider=provider, model=model, ok=True, latency_ms=(time.monotonic() - started) * 1000
    )
    if charge:
        await _charge(
            scope,
            tenant,
            resp=resp,
            role=role,
            model=str(getattr(request, "model", "") or ""),
            goal_id=goal_id,
        )
    return resp
