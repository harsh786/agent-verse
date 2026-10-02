"""POL-04 / POL-05: tenant policies are loaded under the tenant's RLS context.

The API replica's startup load read ``governance_policies`` with no tenant GUC
(0 rows under the least-privilege role) — after a restart in-process goals ran
with no tenant deny/approval policy. Propagation relied on pub/sub alone, so a
replica that missed a change kept a stale policy set forever. In-process runs
now (re)load the tenant's slice strictly when missing or stale.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.governance.policies import Policy, PolicyEngine, PolicyResult
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-pol-lazy", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _Engine(PolicyEngine):
    def __init__(self, fail: bool = False) -> None:
        super().__init__()
        self.loads = 0
        self.fail = fail

    async def reload_from_db(
        self, db: Any, tenant_id: str | None = None, *, strict: bool = False
    ) -> int:
        self.loads += 1
        assert strict and tenant_id == T.tenant_id
        if self.fail:
            raise ConnectionError("db down")
        async with self.lock:
            self._policies = [p for p in self._policies if p.tenant_id != tenant_id]
            self._policies.append(
                Policy(name="deny-web", tenant_id=tenant_id, denied_tools=["web_search"])
            )
        return 1


async def test_first_use_loads_the_tenant_and_caches_it() -> None:
    eng = _Engine()
    await eng.ensure_tenant_loaded(object(), T.tenant_id)
    await eng.ensure_tenant_loaded(object(), T.tenant_id)
    assert eng.loads == 1
    assert eng.evaluate("web_search", tenant_ctx=T) == PolicyResult.DENY


async def test_a_stale_slice_is_resynced() -> None:
    eng = _Engine()
    await eng.ensure_tenant_loaded(object(), T.tenant_id)
    await eng.ensure_tenant_loaded(object(), T.tenant_id, max_age_s=0.0)
    assert eng.loads == 2  # a replica that missed a pub/sub change catches up


async def test_a_failed_first_load_fails_closed() -> None:
    eng = _Engine(fail=True)
    await eng.ensure_tenant_loaded(object(), T.tenant_id)
    assert eng.evaluate("read_file", tenant_ctx=T) == PolicyResult.DENY
    other = TenantContext(tenant_id="t-other", plan=PlanTier.FREE, api_key_id="k")
    assert eng.evaluate("read_file", tenant_ctx=other) != PolicyResult.DENY


async def test_a_failed_refresh_keeps_the_last_known_policies() -> None:
    eng = _Engine()
    await eng.ensure_tenant_loaded(object(), T.tenant_id)
    eng.fail = True
    await eng.ensure_tenant_loaded(object(), T.tenant_id, max_age_s=0.0)
    assert eng.evaluate("web_search", tenant_ctx=T) == PolicyResult.DENY
    assert eng.evaluate("read_file", tenant_ctx=T) != PolicyResult.DENY


async def test_in_process_run_refreshes_the_tenant_policies() -> None:
    from types import SimpleNamespace

    from app.services.goal_service import GoalService

    eng = _Engine()
    svc = GoalService.__new__(GoalService)
    svc._app_state = SimpleNamespace(policy_engine=eng)
    svc._db = object()
    await svc._refresh_tenant_policies(T)
    assert eng.loads == 1


def test_startup_no_longer_reads_policies_without_a_tenant_guc() -> None:
    import inspect

    import app.main as main_mod

    assert '"FROM governance_policies"' not in inspect.getsource(main_mod)


@pytest.mark.integration
async def test_tenant_policy_loads_under_the_app_role(pg_url: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from tests.memory._pg import app_role_engine, sessionmaker_for

    tenant = f"t-pl-{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(pg_url)
    async with admin.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO governance_policies "
                "(id, tenant_id, name, tools_pattern, action, priority, description) "
                "VALUES (:id, :t, 'deny web', 'web_search', 'deny', 100, '')"
            ),
            {"id": uuid.uuid4().hex, "t": tenant},
        )
    await admin.dispose()
    engine = await app_role_engine(pg_url, ["governance_policies", "tenant_settings"])
    try:
        eng = PolicyEngine()
        await eng.ensure_tenant_loaded(sessionmaker_for(engine), tenant)
        ctx = TenantContext(tenant_id=tenant, plan=PlanTier.FREE, api_key_id="k")
        assert eng.evaluate("web_search", tenant_ctx=ctx) == PolicyResult.DENY
    finally:
        await engine.dispose()
