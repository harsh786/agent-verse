"""a03-F058-01: an audit write that exhausts its retries is parked and replayed.

``AuditLog.record`` (the executor's sync entry point) retried a failed INSERT
and then only logged and counted it (``audit_write_lost``) — no durable outbox.
It is now parked in a Redis list and replayed by the drain-audit-write-outbox
beat task; the INSERT is idempotent on the event id.
"""

from __future__ import annotations

import json

import fakeredis
import pytest

from app.governance import audit_outbox
from app.governance.audit import AuditEvent, AuditLog
from app.governance.audit_outbox import (
    AUDIT_OUTBOX_DEAD_KEY,
    AUDIT_OUTBOX_KEY,
    drain_audit_outbox,
    park_audit_row,
)
from app.governance.permissions import ActionLevel
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="audit-outbox-t1", plan=PlanTier.ENTERPRISE, api_key_id="ak1")


def _evt(goal: str = "g-outbox") -> AuditEvent:
    return AuditEvent(
        goal_id=goal,
        tool_name="jira.create_issue",
        action_level=ActionLevel.ALLOW_LOG,
        outcome="step_complete",
        step_id="s1",
        api_key_id="ak1",
        request_id="req-1",
    )


class _DownLog(AuditLog):
    """A DB-backed AuditLog whose every INSERT fails (Postgres outage)."""

    def __init__(self) -> None:
        super().__init__(db_session_factory=object(), retry_base_delay=0, siem_outbox=False)

    async def _db_record(self, event: AuditEvent, tenant_id: str) -> None:
        raise ConnectionError("postgres down")


class _RecordingLog:
    def __init__(self, fail: bool = False) -> None:
        self.written: list[tuple[str, str]] = []
        self.fail = fail

    async def _db_record(self, event: AuditEvent, tenant_id: str) -> None:
        if self.fail:
            raise ConnectionError("still down")
        self.written.append((event.event_id, tenant_id))


async def test_exhausted_record_write_is_parked_not_lost() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    log = _DownLog()
    log.set_outbox_redis(redis)
    evt = _evt()
    log.record(evt, tenant_ctx=T)
    lost = await log.flush()
    assert lost == 0
    assert log.parked_writes == 1
    [raw] = await redis.lrange(AUDIT_OUTBOX_KEY, 0, -1)
    env = json.loads(raw)
    assert env["tenant_id"] == T.tenant_id
    assert env["row"]["id"] == evt.event_id
    assert env["row"]["tool_name"] == "jira.create_issue"


async def test_without_an_outbox_the_write_is_still_counted_lost() -> None:
    log = _DownLog()
    log.record(_evt(), tenant_ctx=T)
    assert await log.flush() == 1
    assert log.parked_writes == 0


async def test_drain_replays_oldest_first_and_stops_at_a_failure() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    first, second = _evt("g1"), _evt("g2")
    from app.governance.audit import _audit_row

    for e in (first, second):
        assert await park_audit_row(redis, tenant_id=T.tenant_id, row=_audit_row(e, T.tenant_id))

    down = _RecordingLog(fail=True)
    assert await drain_audit_outbox(down, redis) == {"replayed": 0, "dead_lettered": 0}
    assert await redis.llen(AUDIT_OUTBOX_KEY) == 2  # nothing dropped

    up = _RecordingLog()
    assert await drain_audit_outbox(up, redis) == {"replayed": 2, "dead_lettered": 0}
    assert up.written == [(first.event_id, T.tenant_id), (second.event_id, T.tenant_id)]
    assert await redis.llen(AUDIT_OUTBOX_KEY) == 0


async def test_a_row_that_keeps_failing_is_dead_lettered_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit_outbox, "AUDIT_OUTBOX_MAX_ATTEMPTS", 2)
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    from app.governance.audit import _audit_row

    e = _evt()
    await park_audit_row(redis, tenant_id=T.tenant_id, row=_audit_row(e, T.tenant_id))
    down = _RecordingLog(fail=True)
    await drain_audit_outbox(down, redis)
    assert await drain_audit_outbox(down, redis) == {"replayed": 0, "dead_lettered": 1}
    assert await redis.llen(AUDIT_OUTBOX_KEY) == 0
    [dead] = await redis.lrange(AUDIT_OUTBOX_DEAD_KEY, 0, -1)
    assert json.loads(dead)["row"]["id"] == e.event_id


async def test_full_outbox_refuses_rather_than_evicting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_outbox, "AUDIT_OUTBOX_MAX", 1)
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    assert await park_audit_row(redis, tenant_id="t", row={"id": "a"}) is True
    assert await park_audit_row(redis, tenant_id="t", row={"id": "b"}) is False
    [raw] = await redis.lrange(AUDIT_OUTBOX_KEY, 0, -1)
    assert json.loads(raw)["row"]["id"] == "a"


@pytest.mark.integration
async def test_parked_row_replays_into_audit_log_under_the_app_role(
    pg_url: str, redis_url: str
) -> None:
    """Real Postgres (NOBYPASSRLS) + real Redis: a parked row lands in audit_log
    under tenant RLS, and a second replay of the same row is a no-op (ON CONFLICT,
    the immutability trigger is never touched)."""
    import redis.asyncio as aioredis

    from app.governance.audit import _audit_row
    from tests.memory._pg import app_role_engine, sessionmaker_for

    engine = await app_role_engine(pg_url, ["audit_log"])
    redis = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await redis.delete(AUDIT_OUTBOX_KEY, AUDIT_OUTBOX_DEAD_KEY)
        log = AuditLog(db_session_factory=sessionmaker_for(engine), siem_outbox=False)
        e = _evt("pg-outbox-goal")
        row = _audit_row(e, T.tenant_id)
        await park_audit_row(redis, tenant_id=T.tenant_id, row=row)
        await park_audit_row(redis, tenant_id=T.tenant_id, row=row)  # a duplicate park
        assert await drain_audit_outbox(log, redis) == {"replayed": 2, "dead_lettered": 0}
        rows = await log.query_db(tenant_ctx=T, goal_id="pg-outbox-goal")
        assert [r.event_id for r in rows] == [e.event_id]
        assert rows[0].tool_name == "jira.create_issue"
        assert rows[0].request_id == "req-1"
    finally:
        await redis.aclose()
        await engine.dispose()
