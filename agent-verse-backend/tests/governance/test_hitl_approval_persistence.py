"""Regression: HITL approvals must be durable and must not linger as phantoms.

Two bugs are covered:
  1. approve()/reject() only mutated in-memory state; the approval_requests row
     stayed 'pending', so startup_restore re-hydrated an approved gate and it
     reappeared in the inbox after a restart.
  2. Approvals whose owning mission/goal already finished are phantoms — the work
     is done, nothing to approve — but they were restored and kept nagging.
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
async def test_approve_persists_and_phantoms_are_expired_on_restore(pg_url: str) -> None:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid.uuid4().hex
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    goal_live = uuid.uuid4().hex  # a goal still executing → approval is real
    goal_done = uuid.uuid4().hex  # a goal that failed → approval is a phantom
    req_live = uuid.uuid4().hex
    req_phantom = uuid.uuid4().hex

    async def _seed() -> None:
        async with factory() as s, s.begin():
            await s.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id}
            )
            await s.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:t,:n,:e)"),
                {"t": tenant_id, "n": "hitl-test", "e": f"{tenant_id}@t.local"},
            )
            await s.execute(
                text("INSERT INTO goals (id, tenant_id, goal_text, status) VALUES (:i,:t,:g,:s)"),
                {"i": goal_live, "t": tenant_id, "g": "live goal", "s": "executing"},
            )
            await s.execute(
                text("INSERT INTO goals (id, tenant_id, goal_text, status) VALUES (:i,:t,:g,:s)"),
                {"i": goal_done, "t": tenant_id, "g": "done goal", "s": "failed"},
            )
            for rid, gid in ((req_live, goal_live), (req_phantom, goal_done)):
                await s.execute(
                    text(
                        "INSERT INTO approval_requests "
                        "(id, tenant_id, goal_id, action, risk_level, status, created_at) "
                        "VALUES (:i,:t,:g,'Org approval gate: financial_commitment','high',"
                        "'pending', NOW())"
                    ),
                    {"i": rid, "t": tenant_id, "g": gid},
                )

    async def _status(req_id: str) -> str | None:
        async with factory() as s:
            return (
                await s.execute(
                    text("SELECT status FROM approval_requests WHERE id=:i"), {"i": req_id}
                )
            ).scalar_one_or_none()

    try:
        await _seed()

        gw = HITLGateway()
        gw._db_session_factory = factory

        # ── Startup maintenance: the phantom (its goal already failed) is expired
        # in the DB; nothing is warm-loaded into memory any more (Postgres is the
        # source of truth and request paths read it per tenant).
        expired = await gw.expire_phantom_approvals(factory)
        assert expired == 1, "only the failed-goal approval is a phantom"
        assert gw._requests == {}, "no cross-tenant warm-up into process memory"
        assert await _status(req_phantom) == "expired", "phantom must be expired in DB"
        assert await _status(req_live) == "pending"

        # ── A process that never raised or cached the request can still approve
        # it: approve_async resolves it in the DB (tenant-scoped) first.
        ok = await gw.approve_async(req_live, approver="tester", tenant_ctx=ctx)
        assert bool(ok) is True
        # CORE-28: a request nobody waits on here is not cached in this process.
        assert (tenant_id, req_live) not in gw._requests
        assert (await gw.aget_request(req_live, tenant_ctx=ctx)).status == (
            ApprovalStatus.APPROVED
        )
        for _ in range(20):
            if await _status(req_live) == "approved":
                break
            await asyncio.sleep(0.1)
        assert await _status(req_live) == "approved", "approval must persist to the DB"

        # ── A fresh gateway must not see the approved gate as pending.
        gw2 = HITLGateway()
        gw2._db_session_factory = factory
        assert await gw2.alist_pending(tenant_ctx=ctx) == []
        assert await gw2.expire_phantom_approvals(factory) == 0
    finally:
        async with factory() as s, s.begin():
            await s.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id}
            )
            await s.execute(
                text("DELETE FROM approval_requests WHERE tenant_id=:t"), {"t": tenant_id}
            )
            await s.execute(text("DELETE FROM goals WHERE tenant_id=:t"), {"t": tenant_id})
            await s.execute(text("DELETE FROM tenants WHERE id=:t"), {"t": tenant_id})
        await engine.dispose()
