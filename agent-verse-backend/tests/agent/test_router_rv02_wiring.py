"""RV-02 (a01-F008-04 / a01-F008-N1): auto-routing must see the durable agents.

create_app built the AgentRouter over the in-memory AgentStore; the lifespan
swapped ``app.state.agent_store`` to the DB-backed store but only gave the
router a DB factory, so every replica routed over an empty cache ("no_agents").
GoalService's fallback called ``agent_store.list()`` (no such method) and the
AttributeError was logged at debug and dropped. The Celery worker's GoalService
(no app state) never routed at all.
"""

from __future__ import annotations

import types
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs

from app.agent.router import MAX_ROUTING_CANDIDATES, AgentRouter
from app.api.agents import AgentStore
from app.governance.audit import AuditLog
from app.governance.hitl import HITLGateway
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-rv02", plan=PlanTier.PROFESSIONAL, api_key_id="k")

_INVOICE_AGENT = {
    "agent_id": "a-invoice",
    "tenant_id": CTX.tenant_id,
    "name": "Invoice reconciler",
    "goal_template": "reconcile vendor invoices against payments",
    "connector_ids": ["builtin-stripe"],
    "created_at": "2026-01-01T00:00:00+00:00",
}


class _DurableStore:
    """Stands in for the DB-backed AgentStore: bounded candidates only.

    It deliberately has no ``list`` / ``list_all``: routing must use a real,
    bounded method of the store.
    """

    _db = object()

    def __init__(self, agents: list[dict[str, Any]], *, fail: bool = False) -> None:
        self.agents = agents
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def routing_candidates(
        self, *, tenant_ctx: TenantContext, goal: str, limit: int
    ) -> list[dict[str, Any]]:
        self.calls.append({"tenant_id": tenant_ctx.tenant_id, "goal": goal, "limit": limit})
        if self.fail:
            raise ConnectionError("agents table unreachable")
        return [a for a in self.agents if a["tenant_id"] == tenant_ctx.tenant_id][:limit]

    async def get_async(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
        return next(
            (
                a
                for a in self.agents
                if a["agent_id"] == agent_id and a["tenant_id"] == tenant_ctx.tenant_id
            ),
            None,
        )


async def test_lifespan_swap_points_the_router_at_the_durable_store() -> None:
    from app.main import _bind_agent_router

    # create_app: the router is built over the never-hydrated in-memory store.
    router = AgentRouter(agent_store=AgentStore())
    state = types.SimpleNamespace(agent_router=router)
    assert (await router.route("reconcile the vendor invoices", CTX)).reason == "no_agents"

    durable = _DurableStore([_INVOICE_AGENT])
    factory = object()
    # The lifespan's swap step.
    _bind_agent_router(state, durable, factory)

    assert router._agent_store is durable
    assert router._db is factory
    router._db = None  # no real DB behind the history query in this unit test
    decision = await router.route("reconcile the vendor invoices with stripe", CTX)
    assert decision.agent_id == "a-invoice"
    assert durable.calls[0]["limit"] == MAX_ROUTING_CANDIDATES


def test_lifespan_calls_the_router_swap() -> None:
    import inspect

    import app.main as main_mod

    src = inspect.getsource(main_mod)
    assert "_bind_agent_router(app.state, _agent_store_with_db, db_factory)" in src


def _svc_with_state(store: Any, router: Any = None) -> GoalService:
    svc = GoalService(audit_log=AuditLog(), hitl=HITLGateway())
    svc._app_state = types.SimpleNamespace(
        agent_store=store, agent_router=router, redis_cost_controller=None, cost_controller=None
    )
    return svc


class _RaisingRouter:
    async def route(self, **_kw: Any) -> Any:
        raise RuntimeError("router exploded")


@pytest.fixture
def fresh_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.goal_service as gs

    monkeypatch.setattr(gs, "_svc_logger", structlog.get_logger(gs.__name__))


async def test_router_failure_fallback_uses_a_real_bounded_store_method(
    fresh_logger: None,
) -> None:
    store = _DurableStore([_INVOICE_AGENT])
    svc = _svc_with_state(store, _RaisingRouter())

    with capture_logs() as logs:
        agent_id, outcome = await svc._auto_route_goal(
            "reconcile the vendor invoices with stripe", CTX
        )

    assert agent_id == "a-invoice"
    assert store.calls and store.calls[0]["limit"] == MAX_ROUTING_CANDIDATES
    assert outcome is not None
    assert outcome["reason"] == "routed_fallback"
    assert outcome["router_error"].startswith("RuntimeError")
    failed = [log for log in logs if log["event"] == "agent_router_failed"]
    assert failed and failed[0]["log_level"] == "warning"


async def test_routing_failure_is_logged_and_recorded_never_swallowed(
    fresh_logger: None,
) -> None:
    store = _DurableStore([_INVOICE_AGENT], fail=True)
    svc = _svc_with_state(store, _RaisingRouter())

    with capture_logs() as logs:
        agent_id, outcome = await svc._auto_route_goal("reconcile invoices", CTX)

    assert agent_id is None
    assert outcome is not None
    assert outcome["agent_id"] is None
    assert outcome["reason"] == "routing_failed"
    assert outcome["router_error"].startswith("RuntimeError")
    assert outcome["fallback_error"].startswith("ConnectionError")
    events = {log["event"]: log for log in logs}
    assert events["agent_router_failed"]["log_level"] == "warning"
    assert events["agent_routing_fallback_failed"]["log_level"] == "warning"


async def test_low_confidence_is_an_honest_no_agent_outcome() -> None:
    """The router's threshold is the policy: no arbitrary 'first agent' pick."""
    store = _DurableStore([_INVOICE_AGENT])
    svc = _svc_with_state(store, AgentRouter(agent_store=store))

    agent_id, outcome = await svc._auto_route_goal("write me a haiku about autumn", CTX)

    assert agent_id is None
    assert outcome is not None and outcome["reason"] == "low_confidence"


async def test_submit_goal_records_the_routing_outcome() -> None:
    store = _DurableStore([_INVOICE_AGENT])
    svc = _svc_with_state(store, AgentRouter(agent_store=store))

    result = await svc.submit_goal(
        goal="reconcile the vendor invoices with stripe",
        priority="normal",
        dry_run=True,
        tenant_ctx=CTX,
    )

    assert result["agent_id"] == "a-invoice"
    record = svc._goals[result["goal_id"]]
    assert record.execution_context["routing_decision"]["agent_id"] == "a-invoice"


async def test_api_routing_decision_is_not_re_routed() -> None:
    """POST /goals already routed (decision in the context): no second route."""
    store = _DurableStore([_INVOICE_AGENT])
    svc = _svc_with_state(store, AgentRouter(agent_store=store))

    result = await svc.submit_goal(
        goal="reconcile the vendor invoices with stripe",
        priority="normal",
        dry_run=True,
        tenant_ctx=CTX,
        execution_context={"routing_decision": {"agent_id": None, "reason": "low_confidence"}},
    )

    assert result.get("agent_id") is None
    assert store.calls == []


async def test_worker_goal_service_routes_over_the_durable_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Celery worker's GoalService (no app state) routes via a DB AgentStore."""
    import app.db.session as session_mod
    from app.scaling.tasks import _build_worker_goal_service

    factory = object()
    monkeypatch.setattr(session_mod, "get_session_factory", lambda: factory)
    seen: list[Any] = []

    async def _candidates(
        self: AgentStore, *, tenant_ctx: TenantContext, goal: str, limit: int
    ) -> list[dict[str, Any]]:
        seen.append((self._db, tenant_ctx.tenant_id, limit))
        return [_INVOICE_AGENT]

    monkeypatch.setattr(AgentStore, "routing_candidates", _candidates)
    monkeypatch.setattr(AgentRouter, "_history_scores_db", _no_history)

    goal_service, db_factory = _build_worker_goal_service()
    assert goal_service is not None and db_factory is factory

    agent_id, outcome = await goal_service._auto_route_goal(
        "reconcile the vendor invoices with stripe", CTX
    )

    assert agent_id == "a-invoice"
    assert outcome is not None and outcome["reason"] == "routed"
    assert seen == [(factory, CTX.tenant_id, MAX_ROUTING_CANDIDATES)]


async def _no_history(self: AgentRouter, agent_ids: list[str], tenant_ctx: Any) -> dict:
    return {}


async def test_db_store_candidate_errors_propagate_not_stale_cache() -> None:
    """A configured DB that fails must not silently route over this replica's cache."""

    def _broken_factory() -> Any:
        raise ConnectionError("db down")

    store = AgentStore(db_session_factory=_broken_factory)
    store._data[(CTX.tenant_id, "stale")] = {**_INVOICE_AGENT, "agent_id": "stale"}

    with pytest.raises(ConnectionError):
        await store.routing_candidates(tenant_ctx=CTX, goal="reconcile invoices", limit=5)
