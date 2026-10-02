"""AUDIT-04: the per-replica audit cache is bounded.

Every recorded event was appended to a per-tenant list in each API replica for
the life of the process. Postgres is the trail (``query_db``); the in-memory
copy is a small bounded cache: at most ``cache_per_tenant`` recent events per
tenant and ``max_cached_tenants`` tenants (least recently written evicted).
"""

from __future__ import annotations

from app.governance.audit import AuditEvent, AuditLog
from app.governance.permissions import ActionLevel
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.FREE, api_key_id="k")


def _evt(i: int) -> AuditEvent:
    return AuditEvent(
        goal_id=f"g{i}", tool_name="t", action_level=ActionLevel.ALLOW_LOG, outcome="ok"
    )


def test_memory_is_bounded_after_100k_records() -> None:
    log = AuditLog(cache_per_tenant=500, max_cached_tenants=50)
    for i in range(100_000):
        log.record(_evt(i), tenant_ctx=_ctx(f"t{i % 200}"))
    assert len(log._log) <= 50
    assert all(len(events) <= 500 for events in log._log.values())


def test_cache_keeps_the_most_recent_events() -> None:
    log = AuditLog(cache_per_tenant=3)
    for i in range(10):
        log.record(_evt(i), tenant_ctx=_ctx("t1"))
    assert [e.goal_id for e in log.query(tenant_ctx=_ctx("t1"))] == ["g7", "g8", "g9"]
