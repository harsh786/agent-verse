"""CORE-28 on real Postgres: reads never grow the HITL cache; waiters still work.

Two gateways share one ``approval_requests`` table (two replicas). Listing and
reading hundreds of pending rows leaves the process-local cache empty, and a
waiter on replica A is released by an approval taken on replica B and then
leaves nothing behind in A's cache.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.governance.hitl import ApprovalStatus, HITLGateway
from app.tenancy.context import PlanTier, TenantContext


@pytest.mark.integration
async def test_reads_do_not_grow_cache_and_cross_replica_waiter_is_released(
    pg_url: str,
) -> None:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid.uuid4().hex
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    goal_id = uuid.uuid4().hex

    async def _tenant_session_exec(sql: str, params: dict[str, object]) -> None:
        async with factory() as s, s.begin():
            await s.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id}
            )
            await s.execute(text(sql), params)

    try:
        await _tenant_session_exec(
            "INSERT INTO tenants (id, name, email) VALUES (:t,:n,:e)",
            {"t": tenant_id, "n": "hitl-cache", "e": f"{tenant_id}@t.local"},
        )
        await _tenant_session_exec(
            "INSERT INTO goals (id, tenant_id, goal_text, status) VALUES (:i,:t,'g','executing')",
            {"i": goal_id, "t": tenant_id},
        )
        await _tenant_session_exec(
            "INSERT INTO approval_requests "
            "(id, tenant_id, goal_id, action, risk_level, status, created_at) "
            "SELECT md5(random()::text || g::text), :t, :g, 'a', 'high', 'pending', NOW() "
            "FROM generate_series(1, 500) AS g",
            {"t": tenant_id, "g": goal_id},
        )

        replica_a = HITLGateway(db_session_factory=factory)
        replica_b = HITLGateway(db_session_factory=factory)

        pending = await replica_a.alist_pending(tenant_ctx=ctx, limit=500)
        assert len(pending) == 500
        for req in pending[:100]:
            assert await replica_a.aget_request(req.request_id, tenant_ctx=ctx) is not None
        assert len(replica_a._requests) == 0

        rid = await replica_a.request_approval_async(
            goal_id=goal_id, action="deploy", tenant_ctx=ctx, require_persisted=True
        )
        waiter = asyncio.create_task(replica_a.wait_for_approval(rid, tenant_ctx=ctx, timeout=20))
        await asyncio.sleep(0.1)
        assert await replica_b.approve_async(rid, approver="ops", tenant_ctx=ctx)
        assert await asyncio.wait_for(waiter, 15) == ApprovalStatus.APPROVED
        assert (tenant_id, rid) not in replica_a._requests
        assert len(replica_b._requests) == 0
    finally:
        await _tenant_session_exec(
            "DELETE FROM approval_requests WHERE tenant_id=:t", {"t": tenant_id}
        )
        await _tenant_session_exec("DELETE FROM goals WHERE tenant_id=:t", {"t": tenant_id})
        await _tenant_session_exec("DELETE FROM tenants WHERE id=:t", {"t": tenant_id})
        await engine.dispose()
