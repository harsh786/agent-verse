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
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.governance.hitl import ApprovalStatus, HITLGateway
from app.tenancy.context import PlanTier, TenantContext

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse",
)


@pytest.mark.integration
async def test_approve_persists_and_phantoms_are_expired_on_restore() -> None:
    engine = create_async_engine(DATABASE_URL)
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

        # ── Restore: the phantom (failed goal) is skipped + expired; the live one loads.
        loaded = await gw.load_pending_from_db_full(factory)
        assert loaded == 1, "only the live-goal approval should be restored"
        assert (tenant_id, req_live) in gw._requests
        assert (tenant_id, req_phantom) not in gw._requests
        assert await _status(req_phantom) == "expired", "phantom must be expired in DB"
        assert await _status(req_live) == "pending"

        # ── Approve the live one → the DB row becomes durable 'approved'.
        ok = await gw.approve(req_live, approver="tester", tenant_ctx=ctx)
        assert bool(ok) is True
        assert gw._requests[(tenant_id, req_live)].status == ApprovalStatus.APPROVED
        # approve() schedules the DB write on the loop — let it settle, then verify.
        for _ in range(20):
            if await _status(req_live) == "approved":
                break
            await asyncio.sleep(0.1)
        assert await _status(req_live) == "approved", "approval must persist to the DB"

        # ── A fresh gateway restoring from DB must NOT resurrect the approved gate.
        gw2 = HITLGateway()
        gw2._db_session_factory = factory
        loaded2 = await gw2.load_pending_from_db_full(factory)
        assert loaded2 == 0, "an approved gate must not reappear after restart"
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
