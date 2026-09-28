"""e2e_full: RANGE-partitioned table maintenance against real Postgres.

``cost_ledger``, ``audit_events``, and ``policy_evaluations`` are each created
as ``PARTITION BY RANGE (created_at)`` with only a fixed, migration-time set of
monthly partitions (2026-01..2027-12) and — before migration 3f2bbce84e68 — no
DEFAULT partition. A RANGE-partitioned table with no matching partition and no
DEFAULT rejects the INSERT outright ("no partition of relation ... found for
row"), which would have hard-failed cost tracking, the audit WAL drain, and
policy-evaluation logging simultaneously the moment real time crossed into
2028, on every replica, with no code change needed to trigger it.

This proves, against a real migrated Postgres:
  1. the DEFAULT partitions exist and actually catch far-future rows instead
     of raising, and
  2. the ``ensure_future_partitions`` maintenance task provisions real monthly
     partitions ahead of the calendar, so DEFAULT stays empty in the steady
     state (a row for a provisioned month lands in ITS partition, not DEFAULT).
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_RANGE_PARTITIONED_TABLES = (
    "cost_ledger",
    "audit_events",
    "policy_evaluations",
    "guardrail_violations",
)


async def test_default_partitions_exist_for_every_range_partitioned_table(
    _migrated_backends: tuple[str, str],
) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    database_url, _ = _migrated_backends
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            for table in _RANGE_PARTITIONED_TABLES:
                row = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM pg_inherits pi "
                            "JOIN pg_class child ON child.oid = pi.inhrelid "
                            "WHERE pi.inhparent = CAST(:parent AS regclass) "
                            "AND child.relname = :default_name"
                        ),
                        {"parent": table, "default_name": f"{table}_default"},
                    )
                ).scalar_one()
                assert row == 1, f"{table} has no DEFAULT partition ({table}_default)"
    finally:
        await engine.dispose()


async def test_far_future_row_lands_in_default_partition_not_rejected(
    _migrated_backends: tuple[str, str],
) -> None:
    """Before the DEFAULT partition existed, this INSERT would raise."""
    import uuid

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    database_url, _ = _migrated_backends
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = f"e2e-partition-{uuid.uuid4().hex[:8]}"
    try:
        async with session_factory() as session, session.begin():
            await session.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            await session.execute(
                text(
                    "INSERT INTO cost_ledger (tenant_id, cost_usd, created_at) "
                    "VALUES (:tid, 0.01, '2031-06-15T00:00:00Z')"
                ),
                {"tid": tenant_id},
            )
            row = (
                await session.execute(
                    text(
                        "SELECT tableoid::regclass::text FROM cost_ledger "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                )
            ).scalar_one()
            assert row == "cost_ledger_default", f"far-future row landed in {row}, not the default"
    finally:
        await engine.dispose()


async def test_ensure_future_partitions_provisions_ahead_of_default(
    _migrated_backends: tuple[str, str],
) -> None:
    """The maintenance task's own DB logic, run directly against real Postgres.

    Partition maintenance is system work: it must open its session on the
    MAINTENANCE role (``get_system_session_factory``), never the application
    role. Under ``E2E_LEAST_PRIVILEGE=1`` the application role is NOBYPASSRLS
    and not the owner, so it can neither use ``system_session`` nor create
    partitions — the task used to run on it and provisioned nothing. The
    application factory is booby-trapped here so any regression back onto it
    fails loudly, and the row is then written by the APPLICATION role to prove
    ordinary tenant writes route into the new partition.
    """
    import os
    import uuid
    from datetime import UTC, datetime

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.scaling.tasks import _ensure_future_partitions

    database_url, _ = _migrated_backends
    # Maintenance DSN when the least-privilege harness provisioned one; otherwise
    # (superuser dev mode) both roles are the same connection, as in production
    # without MAINTENANCE_DATABASE_URL.
    maint_url = os.environ.get("MAINTENANCE_DATABASE_URL") or database_url
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    maint_engine = create_async_engine(maint_url)
    maint_factory = async_sessionmaker(maint_engine, expire_on_commit=False)

    import app.db.session as _dbsession_mod

    def _app_role_forbidden() -> None:
        raise AssertionError(
            "partition maintenance opened its session on the application role; "
            "it must use get_system_session_factory() (the maintenance role)"
        )

    prev_factory = _dbsession_mod.get_session_factory
    prev_system_factory = _dbsession_mod.get_system_session_factory
    _dbsession_mod.get_session_factory = _app_role_forbidden  # type: ignore[assignment]
    _dbsession_mod.get_system_session_factory = lambda: maint_factory  # type: ignore[assignment]
    try:
        result = await _ensure_future_partitions()
        assert "error" not in result, result
        # A month 3 months out from now must have been created for cost_ledger.
        now = datetime.now(UTC)
        future_month = now.month + 3
        future_year = now.year + (0 if future_month <= 12 else 1)
        future_month = ((future_month - 1) % 12) + 1
        expected = f"cost_ledger_{future_year}_{future_month:02d}"
        assert expected in result["created"]["cost_ledger"], (
            result["created"]["cost_ledger"],
            result.get("errors"),
        )
        # Restore the real application factory accessor before the app-role write.
        _dbsession_mod.get_session_factory = prev_factory  # type: ignore[assignment]

        tenant_id = f"e2e-partition-fut-{uuid.uuid4().hex[:8]}"
        target_dt = datetime(future_year, future_month, 10, tzinfo=UTC)
        async with session_factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id}
            )
            await session.execute(
                text(
                    "INSERT INTO cost_ledger (tenant_id, cost_usd, created_at) "
                    "VALUES (:tid, 0.01, :dt)"
                ),
                {"tid": tenant_id, "dt": target_dt},
            )
            row = (
                await session.execute(
                    text(
                        "SELECT tableoid::regclass::text FROM cost_ledger WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                )
            ).scalar_one()
            assert row == expected, (
                f"provisioned-month row landed in {row}, not its dedicated partition "
                f"{expected} — ensure_future_partitions did not actually keep DEFAULT empty"
            )
    finally:
        _dbsession_mod.get_session_factory = prev_factory  # type: ignore[assignment]
        _dbsession_mod.get_system_session_factory = prev_system_factory  # type: ignore[assignment]
        await engine.dispose()
        await maint_engine.dispose()
