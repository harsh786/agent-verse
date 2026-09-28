"""e2e_full: cross-tenant system jobs run on the maintenance role, against real Postgres.

Production runs the API as a NOBYPASSRLS role. ``system_session`` on that role
does not raise — the *next statement* does ("query would be affected by
row-level security policy"), so every beat scanner / retention job in
``app/scaling/tasks.py`` and the workflow run store's maintenance methods
failed in production while passing against a superuser dev database.

Run with ``E2E_LEAST_PRIVILEGE=1`` to get the production posture: the
application role is ``agentverse_app_rls`` (NOBYPASSRLS, DML only) and the
maintenance role is ``agentverse_maint`` (BYPASSRLS), exposed as
``MAINTENANCE_DATABASE_URL``. Without it every connection is the container
superuser and these tests still check the logic, but cannot see RLS.

Pinned here:
  * the stuck-goal scan and HITL expiry act on rows of EVERY tenant through the
    maintenance role (and, least-privilege only, are refused on the app role);
  * the timeout-notification read of the expired approvals spans tenants too;
  * the goal dead-letter write is per-tenant: it succeeds on the APPLICATION
    role inside the goal's own RLS context and cannot touch another tenant's
    goal — no maintenance role involved;
  * the workflow run store's webhook-DLQ scan reads every tenant's events via
    the maintenance factory, while tenant reads stay isolated.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import timedelta
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


def _least_privilege() -> bool:
    return os.getenv("E2E_LEAST_PRIVILEGE", "").lower() in ("1", "true", "yes")


def _forbidden(what: str) -> Any:
    def _raise() -> None:
        raise AssertionError(what)

    return _raise


@pytest_asyncio.fixture(loop_scope="session")
async def roles(
    _backends: tuple[str, str], _migrated_backends: tuple[str, str]
) -> AsyncIterator[SimpleNamespace]:
    """Session factories for the owner (seeding), application and maintenance roles."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    owner_url, _ = _backends
    app_url, _ = _migrated_backends
    maint_url = (os.environ.get("MAINTENANCE_DATABASE_URL") if _least_privilege() else None) or (
        app_url
    )
    engines = [create_async_engine(u) for u in (owner_url, app_url, maint_url)]
    owner, app, maint = (async_sessionmaker(e, expire_on_commit=False) for e in engines)
    try:
        yield SimpleNamespace(owner=owner, app=app, maint=maint)
    finally:
        for e in engines:
            await e.dispose()


async def _seed_tenants(owner: Any, *tenant_ids: str) -> None:
    from sqlalchemy import text

    async with owner() as s, s.begin():
        for tid in tenant_ids:
            await s.execute(
                text(
                    "INSERT INTO tenants (id, name, email) VALUES (:t, 'sysjobs', :e) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"t": tid, "e": f"{tid}@sysjobs.test"},
            )


async def _goal_status(owner: Any, goal_id: str) -> tuple[str, str | None]:
    from sqlalchemy import text

    async with owner() as s:
        row = (
            await s.execute(
                text("SELECT status, error_message FROM goals WHERE id = :g"), {"g": goal_id}
            )
        ).one()
    return str(row[0]), row[1]


async def test_stuck_goal_scan_fails_stuck_goals_of_every_tenant(
    roles: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text

    import app.db.session as dbs
    from app.scaling.tasks import _find_and_fail_stuck_goals

    t1, t2 = uuid.uuid4().hex, uuid.uuid4().hex
    stuck1, stuck2, fresh = (uuid.uuid4().hex for _ in range(3))
    await _seed_tenants(roles.owner, t1, t2)
    async with roles.owner() as s, s.begin():
        for gid, tid, status, age in (
            # asyncpg binds CAST(:x AS interval) from a timedelta, not a string.
            (stuck1, t1, "executing", timedelta(hours=2)),
            (stuck2, t2, "planning", timedelta(hours=3)),
            (fresh, t1, "executing", timedelta(minutes=1)),
        ):
            await s.execute(
                text(
                    "INSERT INTO goals (id, tenant_id, goal_text, status, updated_at) "
                    "VALUES (:g, :t, 'sysjobs', :s, NOW() - CAST(:age AS interval))"
                ),
                {"g": gid, "t": tid, "s": status, "age": age},
            )

    monkeypatch.setattr(dbs, "get_system_session_factory", lambda: roles.maint)
    monkeypatch.setattr(
        dbs, "get_session_factory", _forbidden("stuck-goal scan used the application role")
    )
    result = await _find_and_fail_stuck_goals()

    assert "error" not in result, result
    assert (await _goal_status(roles.owner, stuck1))[0] == "failed"
    assert (await _goal_status(roles.owner, stuck2))[0] == "failed"
    assert (await _goal_status(roles.owner, fresh))[0] == "executing"


async def test_system_scan_is_refused_on_the_application_role(
    roles: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why the maintenance role exists: on the NOBYPASSRLS role the scan fails."""
    if not _least_privilege():
        pytest.skip("needs E2E_LEAST_PRIVILEGE=1 (a superuser bypasses RLS)")
    import app.db.session as dbs
    from app.scaling.tasks import _find_and_fail_stuck_goals

    monkeypatch.setattr(dbs, "get_system_session_factory", lambda: roles.app)
    result = await _find_and_fail_stuck_goals()
    assert "row-level security" in result.get("error", ""), result


async def test_dead_letter_write_is_tenant_scoped_on_the_application_role(
    roles: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text

    import app.db.session as dbs
    from app.scaling.tasks import _update_goal_dlq

    owner_tenant, other_tenant = uuid.uuid4().hex, uuid.uuid4().hex
    goal_id = uuid.uuid4().hex
    await _seed_tenants(roles.owner, owner_tenant, other_tenant)
    async with roles.owner() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO goals (id, tenant_id, goal_text, status) "
                "VALUES (:g, :t, 'sysjobs dlq', 'executing')"
            ),
            {"g": goal_id, "t": owner_tenant},
        )

    monkeypatch.setattr(dbs, "get_session_factory", lambda: roles.app)
    monkeypatch.setattr(
        dbs,
        "get_system_session_factory",
        _forbidden("a single goal's DLQ write used the maintenance role"),
    )

    # Another tenant's context cannot dead-letter this goal.
    await _update_goal_dlq(goal_id, other_tenant, "wrong tenant")
    status, error = await _goal_status(roles.owner, goal_id)
    assert status == "executing"
    assert "wrong tenant" not in (error or "")

    await _update_goal_dlq(goal_id, owner_tenant, "retries exhausted")
    assert await _goal_status(roles.owner, goal_id) == (
        "failed",
        "Dead lettered: retries exhausted",
    )


async def test_hitl_expiry_and_timeout_notifications_span_tenants(
    roles: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text

    import app.db.session as dbs
    from app.scaling.tasks import _expire_db_approvals, _notify_expired_approvals

    t1, t2 = uuid.uuid4().hex, uuid.uuid4().hex
    r1, r2, live = (uuid.uuid4().hex for _ in range(3))
    await _seed_tenants(roles.owner, t1, t2)
    async with roles.owner() as s, s.begin():
        for rid, tid, expires in (
            (r1, t1, timedelta(minutes=-1)),
            (r2, t2, timedelta(minutes=-5)),
            (live, t1, timedelta(hours=1)),
        ):
            await s.execute(
                text(
                    "INSERT INTO approval_requests "
                    "(id, tenant_id, goal_id, action, risk_level, status, created_at, "
                    " expires_at) "
                    "VALUES (:i, :t, :g, 'deploy', 'high', 'pending', NOW(), "
                    "        NOW() + CAST(:exp AS interval))"
                ),
                {"i": rid, "t": tid, "g": f"goal-{rid[:8]}", "exp": expires},
            )

    monkeypatch.setattr(dbs, "get_system_session_factory", lambda: roles.maint)
    monkeypatch.setattr(
        dbs, "get_session_factory", _forbidden("HITL expiry used the application role")
    )
    expired = set(await _expire_db_approvals())
    assert {r1, r2} <= expired, expired
    assert live not in expired

    notif = SimpleNamespace(notify_approval_timeout=AsyncMock(return_value=None))
    fake_main = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(notification_service=notif)))
    monkeypatch.setitem(sys.modules, "app.main", fake_main)
    notified = await _notify_expired_approvals([r1, r2])

    assert sorted(notified) == sorted([r1, r2])
    tenants = {c.kwargs["tenant_id"] for c in notif.notify_approval_timeout.await_args_list}
    assert tenants == {t1, t2}


async def test_run_store_webhook_dlq_scan_spans_tenants_via_maintenance_role(
    roles: SimpleNamespace,
) -> None:
    from sqlalchemy import text

    from app.workflow.run_store import PostgresWorkflowRunStore

    t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())
    e1, e2 = str(uuid.uuid4()), str(uuid.uuid4())
    async with roles.owner() as s, s.begin():
        for eid, tid in ((e1, t1), (e2, t2)):
            await s.execute(
                text(
                    "INSERT INTO workflow_webhook_events "
                    "(id, tenant_id, webhook_token, payload, status, attempts, received_at) "
                    "VALUES (CAST(:e AS uuid), CAST(:t AS uuid), 'tok', '{\"k\": 1}', "
                    "        'failed', 0, NOW() - interval '100 years')"
                ),
                {"e": eid, "t": tid},
            )

    try:
        store = PostgresWorkflowRunStore(roles.app, system_db_factory=roles.maint)
        events = {e["id"]: e for e in await store.get_retryable_webhooks(max_attempts=3)}
        assert {e1, e2} <= set(events), sorted(events)
        assert events[e1]["tenant_id"] == t1 and events[e2]["tenant_id"] == t2

        # Retention runs on the same maintenance factory without error.
        assert await store.delete_expired_runs() >= 0

        if _least_privilege():
            # And the application role alone cannot perform the cross-tenant scan.
            app_only = PostgresWorkflowRunStore(roles.app, system_db_factory=roles.app)
            with pytest.raises(Exception, match="row-level security"):
                await app_only.get_retryable_webhooks(max_attempts=3)
    finally:
        async with roles.owner() as s, s.begin():
            await s.execute(
                text(
                    "DELETE FROM workflow_webhook_events "
                    "WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"
                ),
                {"a": e1, "b": e2},
            )
