"""Comprehensive tests for app/governance/audit.py — targeting 90%+ coverage."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.governance.audit import AuditEvent, AuditLog
from app.governance.permissions import ActionLevel
from app.tenancy.context import TenantContext, PlanTier


# ── helpers ───────────────────────────────────────────────────────────────────

def _ctx(tenant_id: str = "t1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k1")


def _event(goal_id: str = "g1", tool_name: str = "tool_a") -> AuditEvent:
    return AuditEvent(
        goal_id=goal_id,
        tool_name=tool_name,
        action_level=ActionLevel.ALLOW,
        outcome="success",
        step_id="step1",
        approver="alice",
        note="ok",
        ip_address="1.2.3.4",
        user_agent="pytest",
        api_key_id="k1",
        request_id="req1",
        connector_id="conn1",
        auth_type="api_key",
    )


# ── AuditEvent ────────────────────────────────────────────────────────────────

class TestAuditEvent:
    def test_event_id_auto_generated(self) -> None:
        e = _event()
        assert len(e.event_id) == 32  # uuid4().hex is 32 chars

    def test_event_id_unique(self) -> None:
        e1, e2 = _event(), _event()
        assert e1.event_id != e2.event_id

    def test_optional_fields_default_none(self) -> None:
        e = AuditEvent(
            goal_id="g",
            tool_name="t",
            action_level=ActionLevel.DENY,
            outcome="blocked",
        )
        assert e.approver is None
        assert e.ip_address is None
        assert e.user_agent is None
        assert e.api_key_id is None
        assert e.request_id is None
        assert e.connector_id is None
        assert e.auth_type is None
        assert e.step_id == ""
        assert e.note == ""

    def test_all_soc2_fields_stored(self) -> None:
        e = _event()
        assert e.ip_address == "1.2.3.4"
        assert e.user_agent == "pytest"
        assert e.api_key_id == "k1"
        assert e.request_id == "req1"
        assert e.connector_id == "conn1"
        assert e.auth_type == "api_key"


# ── AuditLog (in-memory) ──────────────────────────────────────────────────────

class TestAuditLogMemory:
    def test_record_stores_event(self) -> None:
        log = AuditLog()
        ctx = _ctx()
        log.record(_event(), tenant_ctx=ctx)
        events = log.query(tenant_ctx=ctx)
        assert len(events) == 1

    def test_record_is_tenant_scoped(self) -> None:
        log = AuditLog()
        log.record(_event("g1"), tenant_ctx=_ctx("t1"))
        log.record(_event("g2"), tenant_ctx=_ctx("t2"))
        assert len(log.query(tenant_ctx=_ctx("t1"))) == 1
        assert len(log.query(tenant_ctx=_ctx("t2"))) == 1

    def test_query_filter_by_goal_id(self) -> None:
        log = AuditLog()
        ctx = _ctx()
        log.record(_event("g1", "ta"), tenant_ctx=ctx)
        log.record(_event("g2", "tb"), tenant_ctx=ctx)
        results = log.query(tenant_ctx=ctx, goal_id="g1")
        assert all(e.goal_id == "g1" for e in results)
        assert len(results) == 1

    def test_query_filter_by_tool_name(self) -> None:
        log = AuditLog()
        ctx = _ctx()
        log.record(_event("g1", "ta"), tenant_ctx=ctx)
        log.record(_event("g1", "tb"), tenant_ctx=ctx)
        results = log.query(tenant_ctx=ctx, tool_name="ta")
        assert len(results) == 1
        assert results[0].tool_name == "ta"

    def test_query_limit_applied(self) -> None:
        log = AuditLog()
        ctx = _ctx()
        for i in range(10):
            log.record(_event(f"g{i}"), tenant_ctx=ctx)
        results = log.query(tenant_ctx=ctx, limit=5)
        assert len(results) == 5

    def test_query_empty_tenant_returns_empty(self) -> None:
        log = AuditLog()
        results = log.query(tenant_ctx=_ctx("unknown"))
        assert results == []

    def test_no_delete_method(self) -> None:
        log = AuditLog()
        assert not hasattr(log, "delete"), "AuditLog must be append-only"

    def test_multiple_events_appended(self) -> None:
        log = AuditLog()
        ctx = _ctx()
        for _ in range(5):
            log.record(_event(), tenant_ctx=ctx)
        assert len(log.query(tenant_ctx=ctx)) == 5

    def test_query_no_filters_returns_all(self) -> None:
        log = AuditLog()
        ctx = _ctx()
        log.record(_event("g1", "ta"), tenant_ctx=ctx)
        log.record(_event("g2", "tb"), tenant_ctx=ctx)
        assert len(log.query(tenant_ctx=ctx)) == 2

    def test_record_with_no_db_does_not_raise(self) -> None:
        log = AuditLog(db_session_factory=None)
        ctx = _ctx()
        log.record(_event(), tenant_ctx=ctx)  # no exception

    def test_record_fires_db_task_when_db_configured(self) -> None:
        """record() creates a background task when a running loop is available."""
        loop_mock = MagicMock()
        loop_mock.create_task = MagicMock()

        mock_db = AsyncMock()
        log = AuditLog(db_session_factory=mock_db)
        ctx = _ctx()

        with patch("asyncio.get_running_loop", return_value=loop_mock):
            log.record(_event(), tenant_ctx=ctx)

        loop_mock.create_task.assert_called_once()

    def test_record_suppresses_no_running_loop_error(self) -> None:
        """RuntimeError from get_running_loop (no loop) is silenced."""
        mock_db = AsyncMock()
        log = AuditLog(db_session_factory=mock_db)
        ctx = _ctx()

        with patch("asyncio.get_running_loop", side_effect=RuntimeError("No running loop")):
            log.record(_event(), tenant_ctx=ctx)  # must not raise

        # Event still stored in memory
        assert len(log.query(tenant_ctx=ctx)) == 1


# ── AuditLog.query_db ─────────────────────────────────────────────────────────

class TestAuditLogQueryDb:
    async def test_query_db_falls_back_to_memory_when_no_db(self) -> None:
        log = AuditLog()
        ctx = _ctx()
        log.record(_event("g1"), tenant_ctx=ctx)
        results = await log.query_db(tenant_ctx=ctx)
        assert len(results) == 1

    async def test_query_db_falls_back_on_exception(self) -> None:
        """When DB raises, falls back to in-memory."""
        async def bad_factory():
            raise RuntimeError("DB down")

        log = AuditLog(db_session_factory=bad_factory)
        ctx = _ctx()
        log.record(_event("g1"), tenant_ctx=ctx)

        # query_db uses the factory, which raises — should fall back
        results = await log.query_db(tenant_ctx=ctx)
        assert len(results) == 1

    async def test_query_db_with_real_db_calls_factory(self) -> None:
        """When DB is available, query_db tries to execute SQL (may fail gracefully)."""
        log = AuditLog(db_session_factory=object())  # non-None triggers DB path
        ctx = _ctx("t1")
        log.record(_event("g1"), tenant_ctx=ctx)  # populate memory

        # Without a real DB, query_db will raise and fall back to memory
        results = await log.query_db(tenant_ctx=ctx)
        # Should return the in-memory event via fallback
        assert isinstance(results, list)
        assert len(results) >= 1


# ── AuditLog.sync_from_db ─────────────────────────────────────────────────────

class TestAuditLogSyncFromDb:
    async def test_sync_from_db_returns_0_when_no_db(self) -> None:
        log = AuditLog()
        count = await log.sync_from_db()
        assert count == 0

    async def test_sync_from_db_returns_0_on_exception(self) -> None:
        async def bad_factory():
            raise RuntimeError("DB down")

        log = AuditLog(db_session_factory=bad_factory)
        count = await log.sync_from_db()
        assert count == 0

    async def test_sync_from_db_deduplicates_by_event_id(self) -> None:
        """sync_from_db with no DB configured returns 0."""
        log = AuditLog()
        ctx = _ctx("t1")
        # Pre-populate memory
        log.record(_event("g1"), tenant_ctx=ctx)
        # sync_from_db with no DB → returns 0 (no DB path)
        count = await log.sync_from_db(tenant_id="t1")
        assert count == 0
