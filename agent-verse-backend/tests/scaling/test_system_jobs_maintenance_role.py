"""Cross-tenant system jobs run on the MAINTENANCE role; per-tenant work does not.

Production connects the API (and the Celery workers' tenant work) as a
NOBYPASSRLS role. ``system_session`` (``SET LOCAL row_security = off``) on that
role makes every following statement fail with "query would be affected by
row-level security policy" — which is how every beat scanner, retention job
and partition job in ``app/scaling/tasks.py`` used to fail in production while
passing against a superuser dev database.

The split this file pins:

* genuinely cross-tenant system work opens its session from
  ``app.db.session.get_system_session_factory()`` (the BYPASSRLS maintenance
  role, ``MAINTENANCE_DATABASE_URL``) and enters ``system_session``;
* per-tenant work (the DLQ write for one goal, creating one tenant's mission)
  stays on the application role inside ``sqlalchemy_rls_context`` — moving it
  onto the maintenance role would be a privilege escalation.

The application factory is booby-trapped in every system-job test so a
regression back onto it fails loudly instead of silently "working" in dev.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── plumbing ─────────────────────────────────────────────────────────────────


def _async_cm(value: Any = None) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=value)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _session(results: list[Any] | None = None, *, default: Any = None) -> MagicMock:
    """A session whose execute() pops ``results`` (then returns ``default``)."""
    queue = list(results or [])
    fallback = default if default is not None else MagicMock(
        fetchall=MagicMock(return_value=[]), rowcount=0
    )

    async def _execute(*_a: Any, **_kw: Any) -> Any:
        return queue.pop(0) if queue else fallback

    session = MagicMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.begin = MagicMock(return_value=_async_cm())
    session.begin_nested = MagicMock(side_effect=lambda: _async_cm())
    session.flush = AsyncMock(return_value=None)
    return session


def _factory(*sessions: MagicMock) -> MagicMock:
    """A sessionmaker stand-in handing out ``sessions`` in order."""
    return MagicMock(side_effect=[_async_cm(s) for s in sessions])


class _Recorder:
    """Stand-in for an RLS context manager that records what it scoped."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    @asynccontextmanager
    async def __call__(self, session: Any, *args: Any):
        self.calls.append((session, *args))
        yield session


def _app_role_forbidden() -> None:
    raise AssertionError(
        "a cross-tenant system job opened its session on the application role; "
        "it must use get_system_session_factory() (the maintenance role)"
    )


def _system_patches(system_factory: MagicMock, sys_session: _Recorder) -> list[Any]:
    return [
        patch("app.db.session.get_system_session_factory", return_value=system_factory),
        patch("app.db.session.get_session_factory", side_effect=_app_role_forbidden),
        patch("app.db.rls.system_session", sys_session),
    ]


def _sql(session: MagicMock) -> list[str]:
    return [str(c.args[0]) for c in session.execute.await_args_list]


# ── async system jobs (maintenance role + system_session) ────────────────────


@pytest.mark.asyncio
async def test_stuck_goal_scan_runs_on_maintenance_role() -> None:
    from app.scaling.tasks import _find_and_fail_stuck_goals

    session = _session([MagicMock(fetchall=MagicMock(return_value=[("g1",), ("g2",)]))])
    sys_session = _Recorder()
    p = _system_patches(_factory(session), sys_session)
    with p[0], p[1], p[2]:
        result = await _find_and_fail_stuck_goals()

    assert result == {"stuck_goals_failed": 2, "goal_ids": ["g1", "g2"]}
    assert sys_session.calls == [(session,)]
    assert "UPDATE goals" in _sql(session)[0]


@pytest.mark.asyncio
async def test_retention_runs_on_maintenance_role() -> None:
    from app.scaling.tasks import _delete_expired_records

    # One session for the partition sweep, then one short transaction per
    # retention batch (a single batch per table here: 4 < batch size).
    # scalar() → False: no tenant-wide legal hold is in force.
    sessions = [
        _session(default=MagicMock(rowcount=4, scalar=MagicMock(return_value=False)))
        for _ in range(5)
    ]
    sys_session = _Recorder()
    p = _system_patches(_factory(*sessions), sys_session)
    with p[0], p[1], p[2]:
        result = await _delete_expired_records(30)

    assert result["deleted"] == {
        "goal_events": 4,
        "decision_traces": 4,
        "trigger_events": 4,
        "memory_records": 4,
    }
    assert sys_session.calls == [(s,) for s in sessions]


@pytest.mark.asyncio
async def test_partition_maintenance_runs_on_maintenance_role() -> None:
    from app.scaling.tasks import _MONTHS_AHEAD, _RANGE_PARTITIONED_TABLES, _ensure_future_partitions

    session = _session()
    sys_session = _Recorder()
    p = _system_patches(_factory(session), sys_session)
    with p[0], p[1], p[2]:
        result = await _ensure_future_partitions()

    assert "error" not in result, result
    assert result["errors"] == {}
    for table in _RANGE_PARTITIONED_TABLES:
        assert len(result["created"][table]) == _MONTHS_AHEAD + 1
    assert sys_session.calls == [(session,)]
    ddl = [sql for sql in _sql(session) if "PARTITION OF" in sql]
    rls = [sql for sql in _sql(session) if "app_apply_parent_rls" in sql]
    assert ddl and len(rls) == len(ddl)  # every new partition gets the parent's RLS
    assert all("PARTITION OF" in sql or "app_apply_parent_rls" in sql for sql in _sql(session))


@pytest.mark.asyncio
async def test_partition_maintenance_reports_ddl_failures_per_partition() -> None:
    """A role that cannot create partitions (e.g. not a member of the owner role)
    must surface per-partition errors, never a silent empty success."""
    from app.scaling.tasks import _ensure_future_partitions

    session = _session()
    session.execute = AsyncMock(side_effect=RuntimeError("must be owner of table cost_ledger"))
    sys_session = _Recorder()
    p = _system_patches(_factory(session), sys_session)
    with p[0], p[1], p[2]:
        result = await _ensure_future_partitions()

    assert all(created == [] for created in result["created"].values())
    assert result["errors"]
    assert all("must be owner" in err for err in result["errors"].values())


@pytest.mark.asyncio
async def test_hitl_expiry_runs_on_maintenance_role() -> None:
    from app.scaling.tasks import _expire_db_approvals

    session = _session([MagicMock(fetchall=MagicMock(return_value=[("a1",)]))])
    sys_session = _Recorder()
    p = _system_patches(_factory(session), sys_session)
    with p[0], p[1], p[2]:
        expired = await _expire_db_approvals()

    assert expired == ["a1"]
    assert sys_session.calls == [(session,)]
    assert "UPDATE approval_requests" in _sql(session)[0]


@pytest.mark.asyncio
async def test_document_retention_runs_on_maintenance_role() -> None:
    from app.scaling.tasks import _expire_stale_documents

    session = _session([MagicMock(fetchall=MagicMock(return_value=[]))])
    sys_session = _Recorder()
    p = _system_patches(_factory(session), sys_session)
    with p[0], p[1], p[2]:
        result = await _expire_stale_documents(90)

    assert result["status"] == "ok", result
    assert sys_session.calls == [(session,)]
    assert "DELETE FROM documents" in _sql(session)[0]


@pytest.mark.asyncio
async def test_system_job_fails_loudly_when_maintenance_role_unavailable() -> None:
    """No silent fallback onto the application role when the system factory fails."""
    from app.scaling.tasks import _find_and_fail_stuck_goals

    with (
        patch(
            "app.db.session.get_system_session_factory",
            side_effect=RuntimeError("maintenance DSN unreachable"),
        ),
        patch("app.db.session.get_session_factory", side_effect=_app_role_forbidden),
    ):
        result = await _find_and_fail_stuck_goals()

    assert result["stuck_goals_failed"] == 0
    assert "maintenance DSN unreachable" in result["error"]


# ── beat tasks: org missions ─────────────────────────────────────────────────


def test_mission_resweep_scans_on_maintenance_role() -> None:
    from app.scaling.tasks import resweep_stuck_missions

    session = _session([MagicMock(fetchall=MagicMock(return_value=[]))])
    sys_session = _Recorder()
    p = _system_patches(_factory(session), sys_session)
    with p[0], p[1], p[2]:
        result = resweep_stuck_missions.run()

    assert result == {"reenqueued": 0}
    assert sys_session.calls == [(session,)]
    assert "FROM org_missions" in _sql(session)[0]


def test_org_schedule_claim_is_system_but_mission_creation_is_tenant_scoped() -> None:
    """The cross-tenant claim runs on the maintenance role; creating the mission
    runs on the application role inside the schedule's own tenant context, and
    the follow-up UPDATE keeps an explicit tenant predicate."""
    from app.scaling.tasks import fire_due_org_mission_schedules

    sched_id = uuid.uuid4()
    tenant_uuid = uuid.uuid4()
    row = SimpleNamespace(
        id=sched_id,
        tenant_id=tenant_uuid,
        org_id="org-1",
        title="Weekly report",
        objective="Summarize the week",
        priority="medium",
        autonomy_level=3,
        dept_id=None,
        cron_expression="0 9 * * 1",
        timezone="UTC",
        publish_config=None,
    )
    claim_session = _session(
        [MagicMock(fetchall=MagicMock(return_value=[row])), MagicMock()]
    )
    mission_session = _session([MagicMock()])
    system_factory = _factory(claim_session)
    app_factory = _factory(mission_session)

    mock_svc = MagicMock()
    mock_svc.create_mission = AsyncMock(
        return_value=SimpleNamespace(id="mission-99", extra_data={})
    )
    mock_svc.update_mission_status = AsyncMock(return_value=None)
    sys_session = _Recorder()
    tenant_ctx = _Recorder()

    with (
        patch("app.db.session.get_system_session_factory", return_value=system_factory),
        patch("app.db.session.get_session_factory", return_value=app_factory),
        patch("app.db.rls.system_session", sys_session),
        patch("app.db.rls.sqlalchemy_rls_context", tenant_ctx),
        patch("app.org.service.OrgService", return_value=mock_svc),
        patch("app.org.service._next_cron_fire", return_value="2024-01-08T09:00:00"),
        patch("redis.from_url", side_effect=Exception("no redis")),
        patch("app.scaling.tasks.execute_org_mission.apply_async") as mock_apply,
    ):
        result = fire_due_org_mission_schedules.run()

    assert result == {"fired": 1}
    mock_apply.assert_called_once()
    # system_session wrapped ONLY the claim, on the maintenance-role session.
    assert sys_session.calls == [(claim_session,)]
    # The mission was created on the application-role session, tenant-scoped.
    assert tenant_ctx.calls == [(mission_session, tenant_uuid.hex)]
    update_calls = [
        c for c in mission_session.execute.await_args_list if "last_mission_id" in str(c.args[0])
    ]
    assert len(update_calls) == 1
    sql, params = str(update_calls[0].args[0]), update_calls[0].args[1]
    assert "tenant_id = :tid" in sql
    assert params["tid"] == tenant_uuid
