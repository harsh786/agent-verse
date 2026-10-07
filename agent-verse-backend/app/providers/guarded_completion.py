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
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from app.providers.circuit_breaker import complete_with_failover

_DEFAULT_TIMEOUT_S = 20.0
_log = logging.getLogger(__name__)

# Platform work that is deliberately not charged to any tenant. A decision call
# outside a goal, with no tenant and no system job, is refused once cost
# services are configured (PROV-05): it used to be silently free.
SYSTEM_JOBS: frozenset[str] = frozenset({"model_probe", "shadow_eval"})


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


def current_charge_tenant_id() -> str | None:
    """The tenant LLM calls made here would be charged to: the running goal's
    tenant, else the request / task tenant scope (None when there is none)."""
    scope = _scope.get()
    tenant = scope.tenant_ctx if scope is not None and scope.tenant_ctx is not None else None
    if tenant is None:
        tenant = _tenant_scope.get()
    tenant_id = getattr(tenant, "tenant_id", None)
    return str(tenant_id) if tenant_id else None


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


@contextlib.contextmanager
def uncharged_platform_call(job: str) -> Iterator[None]:
    """Run decision calls as an uncharged platform system job.

    Clears any goal / tenant charge scope (so a background job spawned from a
    request or a goal is not billed to that tenant) and enters
    :func:`system_job_scope` (``job`` must be allowlisted).
    """
    goal_token = _scope.set(None)
    tenant_token = _tenant_scope.set(None)
    try:
        with system_job_scope(job):
            yield
    finally:
        _tenant_scope.reset(tenant_token)
        _scope.reset(goal_token)


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


def vision_timeout_seconds() -> float:
    """Per-model timeout for a vision / OCR call (image in, text out).

    ``AGENTVERSE_VISION_CALL_TIMEOUT_SECONDS``, else the general generation
    timeout (60s). Large hosted vision models can be far slower than chat
    models (NVIDIA's hosted Llama 3.2 90B Vision answered in ~200s on
    2026-10-06), so this is its own knob: raise it to keep a slow preferred
    model, or leave it and the next model in the OCR / vision order takes over.
    """
    raw = os.getenv("AGENTVERSE_VISION_CALL_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return generation_timeout_seconds()
    try:
        return float(raw)
    except ValueError:
        return generation_timeout_seconds()


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


def platform_cost_controller() -> Any:
    """The registered process cost controller (API lifespan / worker), or None.

    Raises :class:`DecisionBudgetExceededError` when the registered resolver
    fails (an unknown budget fails closed).
    """
    return _platform()[0]


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


@dataclass(frozen=True)
class _Reservation:
    """Budget reserved for one decision call before it ran (see :func:`_reserve`)."""

    controller: Any
    tenant_ctx: Any
    goal_id: str
    amount: float


async def _reserve(
    scope: _ChargeScope | None, tenant: Any, amount: float, *, goal_id: str
) -> _Reservation | None:
    """Reserve ``amount`` USD of the goal / tenant budget before the call.

    The preflight only asks "any budget left?": N concurrent calls (the OCR
    vision fallback runs up to OCR_VISION_CONCURRENCY at once) all passed it
    before the first was charged, and together overshot the budget. A
    reservation is an atomic check-and-record on the cost controller (a Redis
    Lua script across replicas), so a call that the remaining budget cannot
    cover is refused BEFORE it runs. None when there is nothing to reserve
    against (no controller, or one that cannot refund).
    """
    from app.agent.nodes.llm_cost import (
        can_reserve_llm_spend,
        charge_goal_id,
        reserve_llm_spend,
    )

    if amount <= 0:
        return None
    if scope is not None:
        controller = getattr(scope.graph, "_cost_controller", None)
        tenant_ctx = scope.tenant_ctx if scope.tenant_ctx is not None else tenant
        reserve_goal = charge_goal_id(scope.agent_state)
    else:
        controller, _ = _platform() if tenant is not None else (None, None)
        tenant_ctx = tenant
        reserve_goal = goal_id
    if tenant_ctx is None or controller is None or not can_reserve_llm_spend(controller):
        return None
    try:
        ok = await reserve_llm_spend(
            controller, goal_id=reserve_goal, tenant_ctx=tenant_ctx, amount=amount
        )
    except Exception as exc:
        # Fail closed: an unknown budget must not let spend through.
        raise DecisionBudgetExceededError(f"budget reservation unavailable: {exc}") from exc
    if not ok:
        if scope is not None:
            context = getattr(scope.agent_state, "context", None)
            if isinstance(context, dict):
                context["_budget_exhausted"] = True
        raise DecisionBudgetExceededError(
            f"LLM budget cannot cover this call (needs up to ${amount:.4f})"
        )
    return _Reservation(controller, tenant_ctx, reserve_goal, amount)


async def _release(reservation: _Reservation | None, reason: str) -> None:
    """Give a reservation back in full (the call failed: nothing to charge)."""
    if reservation is None:
        return
    from app.agent.nodes.llm_cost import refund_llm_spend

    await refund_llm_spend(
        reservation.controller,
        goal_id=reservation.goal_id,
        tenant_ctx=reservation.tenant_ctx,
        amount=reservation.amount,
        reason=reason,
    )


async def _charge(
    scope: _ChargeScope | None,
    tenant: Any,
    *,
    resp: Any,
    role: str,
    model: str,
    goal_id: str | None,
    reserved_usd: float = 0.0,
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
            reserved_usd=reserved_usd,
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
        shim, resp=resp, role=role, model=model, agent_state=state, tenant_ctx=tenant,
        reserved_usd=reserved_usd,
    )
    if state.context.get("_budget_exhausted"):
        raise DecisionBudgetExceededError("tenant LLM budget exhausted by this call")


async def _traced_call(
    provider: Any,
    request: Any,
    role: str,
    timeout: float,
    fallback_models: Sequence[str] = (),
) -> Any:
    """complete_with_failover inside a GenAI span (PROV-23): decision calls were
    untraced. A provider that is already a TracedProvider records its own span."""
    from app.observability.traced_provider import TracedProvider

    if isinstance(provider, TracedProvider):
        return await complete_with_failover(
            provider, request, timeout_seconds=timeout, fallback_models=fallback_models
        )
    from app.observability.genai import record_generation
    from app.observability.traced_provider import provider_system_of, record_response

    async with record_generation(
        request, provider_system=provider_system_of(provider), role=role
    ) as rec:
        resp = await complete_with_failover(
            provider, request, timeout_seconds=timeout, fallback_models=fallback_models
        )
        record_response(rec, resp)
        return resp


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


def _route_unset_model(request: Any, role: str, provider: Any) -> tuple[Any, list[str]]:
    """Give a known role's call with no model the role's model (role_preference).

    Callers across the runtime send ``model=""`` ("provider default") — on a
    deployment whose env default is a cloud model, every such role (agent
    router, goal classifier, debate, judges, guardrails, memory, RAG strategy
    LLMs, ...) ignored the operator's saved order and ran in the cloud. Only a
    role listed in ``role_preference.ROLE_TASK_TYPES`` is routed, only to a model
    the provider can actually serve, and the provider's own default stays the
    last fallback. Never raises: the request is returned unchanged on any doubt.
    """
    import dataclasses

    try:
        from app.ai_router.role_preference import resolve_role_model, role_task_type

        task = role_task_type(role)
        if not task or not dataclasses.is_dataclass(request):
            return request, []
        model = resolve_role_model(role, provider=provider)
        default = str(getattr(provider, "_default_model", "") or "")
        if not model or model == default:
            return request, []
        from app.providers.model_dispatch import can_serve_model

        if not can_serve_model(provider, model):
            return request, []
        from app.ai_router.role_preference import preferred_model_and_fallbacks

        # The rest of the saved order, then the provider default (an env pin
        # without a saved order fails over to the provider default only).
        ranked, fallbacks = preferred_model_and_fallbacks(task, provider)
        if ranked != model:
            fallbacks = [default] if default else []
        return dataclasses.replace(request, model=model), fallbacks  # type: ignore[type-var]
    except Exception:  # pragma: no cover - never block a call over routing
        return request, []


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
    fallback_models: Sequence[str] = (),
    reserve_usd: float = 0.0,
) -> Any:
    """``provider.complete(request)`` with circuit breaker, timeout and cost charging.

    ``reserve_usd`` (> 0): the most this call may cost. It is reserved from the
    goal / tenant budget atomically BEFORE the call (refused with
    :class:`DecisionBudgetExceededError` when the remaining budget cannot cover
    it), settled against the real cost afterwards and given back if the call
    fails — so concurrent calls can never together spend past the budget.

    ``fallback_models`` are tried in order when ``request.model`` fails (down,
    timing out, empty answer) — see :func:`complete_with_failover`. The model that
    actually answered is the one charged and recorded.

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
    if not str(getattr(request, "model", "") or ""):
        request, role_fallbacks = _route_unset_model(request, role, provider)
        if role_fallbacks and not fallback_models:
            fallback_models = role_fallbacks
    scope = _scope.get()
    tenant = _tenant(tenant_ctx, tenant_id)
    reservation: _Reservation | None = None
    if charge:
        _require_attribution(scope, tenant)
        await _preflight(scope, tenant)
        if reserve_usd > 0:
            # Reserve and settle under ONE goal id (out of a goal: a fresh one).
            goal_id = goal_id or f"decision:{role}:{uuid.uuid4().hex}"
            reservation = await _reserve(scope, tenant, reserve_usd, goal_id=goal_id)
    import time

    from app.ai_router.health_feed import record_llm_outcome

    model = str(getattr(request, "model", "") or "")
    started = time.monotonic()
    try:
        resp = await _traced_call(
            provider, request, role, _timeout(timeout_seconds), fallback_models
        )
    except BaseException as exc:
        await _release(reservation, f"{role}:call_failed")
        if not isinstance(exc, Exception):
            raise
        if getattr(exc, "provider_failure", True):
            record_llm_outcome(
                provider=provider, model=model, ok=False,
                latency_ms=(time.monotonic() - started) * 1000, error=str(exc),
            )
        raise
    _latency_ms = (time.monotonic() - started) * 1000
    # After a failover the answer came from another model: record and charge that one.
    answered = str(getattr(resp, "model", "") or "")
    if fallback_models and answered in fallback_models:
        model = answered
    record_llm_outcome(provider=provider, model=model, ok=True, latency_ms=_latency_ms)
    # PROV-24: sampled shadow of a candidate model (flag-gated, uncharged, metered).
    from app.ai_router.shadow_router import maybe_fire_shadow

    maybe_fire_shadow(provider, request, role=role, primary=resp, primary_latency_ms=_latency_ms)
    if charge:
        await _charge(
            scope,
            tenant,
            resp=resp,
            role=role,
            model=model,
            goal_id=goal_id,
            reserved_usd=reservation.amount if reservation is not None else 0.0,
        )
    return resp
