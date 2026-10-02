"""AUDIT-06: SIEM forwarding from a durable outbox, for API and worker events alike.

Forwarding used to be a per-process deque in the API only: a failed batch was
dropped, a restart lost the buffer, and worker audit events never reached the
SIEM. Now every ``AuditLog`` write (API or worker) inserts an
``audit_siem_outbox`` row IN THE SAME TRANSACTION as the ``audit_log`` row, and
one Celery beat consumer drains it (``FOR UPDATE SKIP LOCKED``, so replicas never
double-send): sent rows are deleted, failed ones retried with backoff, and rows
that keep failing are parked as ``dead`` (DLQ) instead of being lost.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.governance.audit import AuditEvent, AuditLog
from app.governance.permissions import ActionLevel
from app.governance.siem_adapters import SIEMAdapter, SIEMConfig
from app.governance.siem_outbox import drain_siem_outbox
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration


class _Adapter(SIEMAdapter):
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.batches: list[list[dict[str, Any]]] = []

    async def send(self, events: list[dict[str, Any]], config: SIEMConfig) -> bool:
        self.batches.append(events)
        return self.ok


def _ctx(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.FREE, api_key_id="k")


def _evt(goal: str) -> AuditEvent:
    return AuditEvent(
        goal_id=goal, tool_name="github", action_level=ActionLevel.ALLOW_LOG, outcome="ok"
    )


async def _outbox(admin: Any, tenant: str) -> list[tuple[Any, ...]]:
    async with admin() as s:
        return list(
            (
                await s.execute(
                    text(
                        "SELECT audit_id, status, attempts FROM audit_siem_outbox "
                        "WHERE tenant_id = :t ORDER BY id"
                    ),
                    {"t": tenant},
                )
            ).all()
        )


async def test_audit_write_enqueues_and_drainer_forwards_retries_and_dead_letters(
    pg_url: str,
) -> None:
    from tests.memory._pg import app_role_engine, sessionmaker_for

    tenant = f"t-siem-{uuid.uuid4().hex[:8]}"
    engine = await app_role_engine(pg_url, ["audit_log", "audit_siem_outbox"])
    admin_engine = create_async_engine(pg_url)
    admin = async_sessionmaker(admin_engine, expire_on_commit=False)
    try:
        # A "worker" AuditLog: same code path as the API's.
        log = AuditLog(db_session_factory=sessionmaker_for(engine), siem_outbox=True)
        e1, e2 = _evt("g1"), _evt("g2")
        await log.record_async(e1, tenant_ctx=_ctx(tenant))
        await log.record_async(e2, tenant_ctx=_ctx(tenant))
        assert [r[0] for r in await _outbox(admin, tenant)] == [e1.event_id, e2.event_id]

        # SIEM down: nothing is lost, attempts climb, rows wait for backoff.
        down = _Adapter(ok=False)
        res = await drain_siem_outbox(admin, down, SIEMConfig(), max_attempts=2)
        assert res["sent"] == 0 and res["failed"] >= 2
        assert all(r[1] == "pending" and r[2] == 1 for r in await _outbox(admin, tenant))

        # Second failure reaches max_attempts → dead-lettered, not dropped.
        async with admin() as s, s.begin():
            await s.execute(text("UPDATE audit_siem_outbox SET next_attempt_at = now()"))
        await drain_siem_outbox(admin, down, SIEMConfig(), max_attempts=2)
        assert all(r[1] == "dead" for r in await _outbox(admin, tenant))

        # A fresh event is forwarded and removed once the SIEM accepts it.
        e3 = _evt("g3")
        await log.record_async(e3, tenant_ctx=_ctx(tenant))
        up = _Adapter(ok=True)
        res = await drain_siem_outbox(admin, up, SIEMConfig(), max_attempts=2)
        sent_ids = [e["id"] for batch in up.batches for e in batch]
        assert e3.event_id in sent_ids
        assert e1.event_id not in sent_ids  # dead rows are not re-sent automatically
        assert [r[0] for r in await _outbox(admin, tenant)] == [e1.event_id, e2.event_id]
    finally:
        await engine.dispose()
        await admin_engine.dispose()


async def test_no_outbox_rows_when_siem_is_not_configured(pg_url: str) -> None:
    from tests.memory._pg import app_role_engine, sessionmaker_for

    tenant = f"t-siem-{uuid.uuid4().hex[:8]}"
    engine = await app_role_engine(pg_url, ["audit_log", "audit_siem_outbox"])
    admin_engine = create_async_engine(pg_url)
    try:
        log = AuditLog(db_session_factory=sessionmaker_for(engine), siem_outbox=False)
        await log.record_async(_evt("g"), tenant_ctx=_ctx(tenant))
        assert await _outbox(async_sessionmaker(admin_engine), tenant) == []
    finally:
        await engine.dispose()
        await admin_engine.dispose()
