"""AUDIT-01 / AUDIT-05: audit DB writes are awaited, retried and never orphaned.

``AuditLog.record`` used to schedule the INSERT in an unreferenced task: a
failure was logged and the event lost, and on Celery workers the pending insert
was cancelled when ``run_in_fresh_loop`` closed the per-task loop. Now:

* ``record_async`` awaits the insert, retries transient failures and raises
  :class:`AuditWriteError` when the event could not be stored;
* ``record`` keeps a strong reference to its write task and ``flush`` awaits
  every pending write (the worker flushes before its loop closes).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.governance.audit import AuditEvent, AuditLog, AuditWriteError
from app.governance.permissions import ActionLevel
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="audit-durable-t1", plan=PlanTier.ENTERPRISE, api_key_id="ak1")


class _Result:
    def fetchall(self) -> list[Any]:
        return []

    def scalar_one_or_none(self) -> Any:
        return None


class _Session:
    def __init__(self, owner: _FlakyFactory) -> None:
        self._owner = owner

    async def __aenter__(self) -> _Session:
        self._owner.attempts += 1
        if self._owner.attempts <= self._owner.fail_first:
            raise ConnectionError("db blip")
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    def begin(self) -> _Session:
        return _Txn(self)  # type: ignore[return-value]

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        if "INSERT INTO audit_log" in sql:
            if self._owner.delay:
                await asyncio.sleep(self._owner.delay)
            self._owner.rows.append(params if params is not None else stmt)
        return _Result()

    def add(self, row: Any) -> None:  # pragma: no cover - ORM path no longer used
        self._owner.rows.append(row)

    async def flush(self) -> None:
        return None


class _Txn:
    def __init__(self, session: _Session) -> None:
        self._s = session

    async def __aenter__(self) -> _Session:
        return self._s

    async def __aexit__(self, *_: object) -> bool:
        return False


class _FlakyFactory:
    def __init__(self, fail_first: int = 0, delay: float = 0.0) -> None:
        self.fail_first = fail_first
        self.attempts = 0
        self.delay = delay
        self.rows: list[Any] = []

    def __call__(self) -> _Session:
        return _Session(self)


def _evt(goal: str = "g1") -> AuditEvent:
    return AuditEvent(
        goal_id=goal, tool_name="github", action_level=ActionLevel.ALLOW_LOG, outcome="ok"
    )


async def test_record_async_retries_transient_failure_and_persists() -> None:
    db = _FlakyFactory(fail_first=2)
    log = AuditLog(db_session_factory=db, retry_base_delay=0)
    await log.record_async(_evt(), tenant_ctx=T)
    assert db.attempts == 3
    assert len(db.rows) == 1


async def test_record_async_raises_when_event_cannot_be_stored() -> None:
    db = _FlakyFactory(fail_first=99)
    log = AuditLog(db_session_factory=db, retry_base_delay=0)
    with pytest.raises(AuditWriteError):
        await log.record_async(_evt(), tenant_ctx=T)
    assert db.rows == []


async def test_record_keeps_strong_reference_and_flush_awaits_pending_writes() -> None:
    db = _FlakyFactory(delay=0.05)
    log = AuditLog(db_session_factory=db, retry_base_delay=0)
    log.record(_evt("a"), tenant_ctx=T)
    log.record(_evt("b"), tenant_ctx=T)
    assert log.pending_writes == 2
    assert db.rows == []
    lost = await log.flush()
    assert lost == 0
    assert len(db.rows) == 2
    assert log.pending_writes == 0


async def test_flush_reports_lost_events() -> None:
    db = _FlakyFactory(fail_first=99)
    log = AuditLog(db_session_factory=db, retry_base_delay=0)
    log.record(_evt(), tenant_ctx=T)
    assert await log.flush() == 1


def test_worker_loop_flushes_audit_before_closing() -> None:
    """A write scheduled by the last step must land before the task loop closes."""
    from app.scaling.tasks import _await_then_flush_audit, _run_async

    db = _FlakyFactory(delay=0.05)
    log = AuditLog(db_session_factory=db, retry_base_delay=0)

    async def _last_step() -> str:
        log.record(_evt("final-step"), tenant_ctx=T)
        return "done"

    assert _run_async(_await_then_flush_audit(_last_step(), log)) == "done"
    assert len(db.rows) == 1


def test_worker_loop_without_flush_would_lose_the_write() -> None:
    """Guard: proves the flush (not luck) is what saves the final write."""
    from app.scaling.tasks import _run_async

    db = _FlakyFactory(delay=0.05)
    log = AuditLog(db_session_factory=db, retry_base_delay=0)

    async def _last_step() -> str:
        log.record(_evt("final-step"), tenant_ctx=T)
        return "done"

    _run_async(_last_step())
    assert db.rows == []


@pytest.mark.integration
async def test_record_async_persists_under_app_role_and_retry_is_idempotent(
    pg_url: str,
) -> None:
    """Real Postgres, NOBYPASSRLS role: the awaited write lands under tenant RLS and a
    re-sent INSERT (retry after an unknown commit outcome) neither duplicates the
    row nor trips the audit_log immutability trigger."""
    from sqlalchemy import text

    from tests.memory._pg import app_role_engine, sessionmaker_for

    engine = await app_role_engine(pg_url, ["audit_log"])
    try:
        factory = sessionmaker_for(engine)
        log = AuditLog(db_session_factory=factory, retry_base_delay=0)
        evt = _evt("pg-goal")
        await log.record_async(evt, tenant_ctx=T)
        await log._db_record(evt, T.tenant_id)  # the retry path
        rows = await log.query_db(tenant_ctx=T, goal_id="pg-goal")
        assert [r.event_id for r in rows] == [evt.event_id]
        async with factory() as s:
            # Without the tenant GUC, RLS hides the row from the app role.
            n = (
                await s.execute(text("SELECT count(*) FROM audit_log WHERE goal_id='pg-goal'"))
            ).scalar_one()
        assert n == 0
    finally:
        await engine.dispose()
