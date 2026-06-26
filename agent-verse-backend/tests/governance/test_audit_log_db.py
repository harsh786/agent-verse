"""Tests for AuditLog DB persistence — dual-write hybrid pattern.

Verifies:
- sync_from_db() returns 0 with no DB factory (no-op)
- DB failures never break in-memory record()
- record() always writes to in-memory log regardless of DB availability
"""

from __future__ import annotations

import asyncio

import pytest

from app.governance.audit import AuditEvent, AuditLog
from app.governance.permissions import ActionLevel
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="audit-db-t1", plan=PlanTier.ENTERPRISE, api_key_id="ak1")


# ── Helper: a session factory whose __aenter__ always raises ──────────────────


def _bad_factory() -> object:
    """Return an async context manager that always raises on __aenter__."""

    class _FailCtx:
        async def __aenter__(self) -> None:
            raise RuntimeError("DB down")

        async def __aexit__(self, *_: object) -> bool:
            return False

    return _FailCtx()


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_audit_log_sync_from_db_noop() -> None:
    """sync_from_db() returns 0 immediately when no DB factory is configured."""
    log = AuditLog()
    count = await log.sync_from_db()
    assert count == 0


async def test_audit_log_sync_from_db_noop_with_tenant_filter() -> None:
    """sync_from_db(tenant_id=...) also returns 0 without a DB factory."""
    log = AuditLog()
    count = await log.sync_from_db(tenant_id="some-tenant")
    assert count == 0


async def test_audit_log_db_factory_error_doesnt_break_record() -> None:
    """DB failure must never prevent the in-memory record from being added."""
    log = AuditLog(db_session_factory=_bad_factory)
    evt = AuditEvent(
        goal_id="g1",
        tool_name="github",
        action_level=ActionLevel.ALLOW_LOG,
        outcome="ok",
    )
    log.record(evt, tenant_ctx=T)

    # Yield to the event loop so the fire-and-forget DB task can run and fail
    # gracefully (caught internally — no unhandled task exception).
    await asyncio.sleep(0)

    # In-memory entry MUST exist regardless of DB failure
    entries = log.query(tenant_ctx=T)
    assert len(entries) == 1
    assert entries[0].goal_id == "g1"
    assert entries[0].tool_name == "github"


async def test_audit_log_multiple_records_with_failing_db() -> None:
    """Multiple records succeed in memory even with a broken DB factory."""
    log = AuditLog(db_session_factory=_bad_factory)

    for i in range(3):
        evt = AuditEvent(
            goal_id=f"goal-{i}",
            tool_name="tool",
            action_level=ActionLevel.ALLOW,
            outcome="ok",
        )
        log.record(evt, tenant_ctx=T)

    # Let all fire-and-forget tasks run
    await asyncio.sleep(0)

    entries = log.query(tenant_ctx=T)
    assert len(entries) == 3


async def test_audit_log_record_no_db_no_task_created() -> None:
    """Without a DB factory, record() is purely synchronous — no tasks."""
    log = AuditLog()  # no db_session_factory
    evt = AuditEvent(
        goal_id="g2",
        tool_name="slack",
        action_level=ActionLevel.DENY,
        outcome="blocked",
    )
    log.record(evt, tenant_ctx=T)

    entries = log.query(tenant_ctx=T)
    assert len(entries) == 1
    assert entries[0].outcome == "blocked"
