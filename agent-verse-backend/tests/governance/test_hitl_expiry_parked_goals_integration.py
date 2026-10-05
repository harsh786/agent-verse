"""NF-11: a supervised goal parked in waiting_human fails when its approval expires.

Before: the expire_hitl_approvals beat marked the request ``timed_out`` and the
P5-4 wake reached only LIVE waiters. A goal whose graph had ended waiting for a
human (parked, suspended, no task) stayed ``waiting_human`` forever.

Runs the real beat task against Postgres + Redis testcontainers. The beat runs
in its own process in production, so passing here means any replica's parked
goal is failed.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


async def _seed(pg_url: str) -> dict[str, str]:
    engine = create_async_engine(pg_url)
    ids: dict[str, str] = {}
    try:
        async with engine.begin() as conn:
            tenant = uuid.uuid4().hex
            other = uuid.uuid4().hex
            for tid in (tenant, other):
                await conn.execute(
                    text(
                        "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                        "VALUES (:id, 'T', :e, 'starter', true)"
                    ),
                    {"id": tid, "e": f"{tid}@example.test"},
                )
            ids["tenant"] = tenant

            async def goal(name: str, tid: str, status: str, suspended: bool) -> str:
                gid = uuid.uuid4().hex
                ctx = {"_suspended_for_approval": True} if suspended else {}
                await conn.execute(
                    text(
                        "INSERT INTO goals (id, tenant_id, goal_text, status, priority, "
                        "autonomy_mode, workflow_mode, execution_context, dry_run, iterations) "
                        "VALUES (:id, :t, 'Deploy the api', :st, 'normal', 'supervised', "
                        "'single_agent', CAST(:ctx AS json), false, 1)"
                    ),
                    {"id": gid, "t": tid, "st": status, "ctx": json.dumps(ctx)},
                )
                ids[name] = gid
                return gid

            async def approval(name: str, tid: str, gid: str, expires_in_s: int) -> None:
                rid = uuid.uuid4().hex
                await conn.execute(
                    text(
                        "INSERT INTO approval_requests (id, tenant_id, goal_id, action, "
                        "risk_level, status, expires_at) VALUES (:id, :t, :g, "
                        "'deploy the api to prod', 'high', 'pending', "
                        "now() + make_interval(secs => :s))"
                    ),
                    {"id": rid, "t": tid, "g": gid, "s": expires_in_s},
                )
                ids[name] = rid

            parked = await goal("parked", tenant, "waiting_human", True)
            await approval("parked_req", tenant, parked, -60)
            # Parked, but the suspended flag write was lost: still parked in the DB.
            unflagged = await goal("unflagged", tenant, "waiting_human", False)
            await approval("unflagged_req", tenant, unflagged, -60)
            # Parked on an approval that has NOT expired yet.
            fresh = await goal("fresh", tenant, "waiting_human", True)
            await approval("fresh_req", tenant, fresh, 3600)
            # A live waiter (row stays executing): P5-4 wakes it, the beat must not fail it.
            live = await goal("live", tenant, "executing", False)
            await approval("live_req", tenant, live, -60)
            # Already resumed on another replica before the beat ran.
            resumed = await goal("resumed", other, "complete", False)
            await approval("resumed_req", other, resumed, -60)
    finally:
        await engine.dispose()
    return ids


async def _goal_row(pg_url: str, gid: str) -> dict[str, Any]:
    engine = create_async_engine(pg_url)
    try:
        async with engine.connect() as conn:
            r = (
                await conn.execute(
                    text(
                        "SELECT status, error_message, execution_context::jsonb, completed_at "
                        "FROM goals WHERE id = :g"
                    ),
                    {"g": gid},
                )
            ).one()
            events = (
                await conn.execute(
                    text(
                        "SELECT event_type, payload FROM goal_events WHERE goal_id = :g "
                        "ORDER BY sequence"
                    ),
                    {"g": gid},
                )
            ).fetchall()
    finally:
        await engine.dispose()
    return {
        "status": r[0],
        "error": r[1],
        "context": r[2],
        "completed_at": r[3],
        "events": [(e[0], e[1]) for e in events],
    }


class _Publisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, Any]]] = []

    def publish(self, channel: str, data: str) -> int:
        self.messages.append((channel, json.loads(data)))
        return 1


def test_beat_fails_parked_goal_when_its_approval_expires(
    test_backends: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.scaling import tasks

    pg_url, _redis_url = test_backends
    ids = asyncio.run(_seed(pg_url))
    publisher = _Publisher()
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: publisher)

    out = tasks.expire_hitl_approvals.run()

    assert out["parked_goals_failed"] == 2
    for name in ("parked", "unflagged"):
        row = asyncio.run(_goal_row(pg_url, ids[name]))
        assert row["status"] == "failed"
        assert row["error"].startswith("approval expired: approval request ")
        assert ids[f"{name}_req"] in row["error"]
        assert "_suspended_for_approval" not in row["context"]
        assert row["completed_at"] is not None
        failed = [p for t, p in row["events"] if t == "goal_failed"]
        assert len(failed) == 1
        payload = failed[0] if isinstance(failed[0], dict) else json.loads(failed[0])
        assert payload["failure_reason"] == "approval_expired"
        channel = f"goal_events:{ids['tenant']}:{ids[name]}"
        assert any(
            ch == channel and m["type"] == "goal_failed" for ch, m in publisher.messages
        )

    assert asyncio.run(_goal_row(pg_url, ids["fresh"]))["status"] == "waiting_human"
    assert asyncio.run(_goal_row(pg_url, ids["live"]))["status"] == "executing"
    resumed = asyncio.run(_goal_row(pg_url, ids["resumed"]))
    assert resumed["status"] == "complete"
    assert resumed["error"] in (None, "")

    # A second beat run (another replica) changes nothing and emits nothing.
    publisher.messages.clear()
    again = tasks.expire_hitl_approvals.run()
    assert again["parked_goals_failed"] == 0
    assert publisher.messages == []
    row = asyncio.run(_goal_row(pg_url, ids["parked"]))
    assert [t for t, _ in row["events"]].count("goal_failed") == 1
