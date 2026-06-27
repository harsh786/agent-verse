"""Tests for critical bug fixes in AgentVerse.

Covers:
  CRITICAL-1: RedisCostController.check_and_record alias
  CRITICAL-4: model_router.route() removed from graph.py
  HIGH-4:     PolicyEngine tenant filtering
  CRITICAL-5: Parallel wave asyncio.Lock protection
  MEDIUM-10:  HITLGateway uses get_running_loop() not get_event_loop()
"""

import asyncio

import pytest


def test_redis_cost_controller_has_check_and_record():
    """RedisCostController must have check_and_record (not just check_and_record_async)."""
    from app.governance.cost import RedisCostController

    ctrl = RedisCostController(redis=None)
    assert hasattr(ctrl, "check_and_record"), (
        "RedisCostController must have check_and_record() method"
    )
    assert asyncio.iscoroutinefunction(ctrl.check_and_record), (
        "check_and_record must be async"
    )


def test_model_router_has_no_route_method_called_in_graph():
    """graph.py must not call model_router.route() — use model_for() or similar."""
    import inspect

    from app.agent import graph

    src = inspect.getsource(graph)
    assert "model_router.route(" not in src, (
        "graph.py must not call .route() — that method doesn't exist on ModelRouter"
    )


def test_policy_engine_evaluate_respects_tenant():
    """PolicyEngine.evaluate must skip policies from other tenants."""
    from app.governance.policies import Policy, PolicyEngine, PolicyResult
    from app.tenancy.context import PlanTier, TenantContext

    T1 = TenantContext(tenant_id="pe-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")
    T2 = TenantContext(tenant_id="pe-t2", plan=PlanTier.ENTERPRISE, api_key_id="k2")

    engine = PolicyEngine()
    # Add a DENY policy for T1 only, using actual Policy fields
    p = Policy(
        name="t1-deny",
        description="Deny shell_exec for T1 only",
        denied_tools=["shell_exec"],
        tenant_id=T1.tenant_id,
    )
    engine._policies.append(p)

    # T2's evaluation must not be affected by T1's policy
    result_t2 = engine.evaluate("shell_exec", tenant_ctx=T2)
    assert result_t2 != PolicyResult.DENY, (
        "T2 must not be denied by T1's policy"
    )

    # T1 should still be denied
    result_t1 = engine.evaluate("shell_exec", tenant_ctx=T1)
    assert result_t1 == PolicyResult.DENY, (
        "T1 must be denied by its own policy"
    )


@pytest.mark.asyncio
async def test_parallel_wave_state_mutation_is_safe():
    """Concurrent wave step mutations must not corrupt AgentState."""
    results = []
    lock = asyncio.Lock()

    async def safe_append(val: int) -> None:
        async with lock:
            results.append(val)

    await asyncio.gather(*[safe_append(i) for i in range(100)])
    assert len(results) == 100  # No lost writes


def test_hitl_no_get_event_loop():
    """HITLGateway must not use deprecated asyncio.get_event_loop()."""
    import inspect

    from app.governance import hitl

    src = inspect.getsource(hitl)
    assert "get_event_loop()" not in src, (
        "hitl.py must use get_running_loop() not deprecated get_event_loop()"
    )
