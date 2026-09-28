"""Range-partition goal_events by month; allocate event sequences from a counter.

goal_events is the highest-volume table (every step / token / status event of
every goal). It was one unpartitioned heap, and retention was a single
``DELETE ... WHERE created_at < cutoff`` over it — at scale one enormous
transaction (WAL spike, bloat, long vacuum) that also got slower as the table
grew. It is now ``PARTITION BY RANGE (created_at)`` with monthly partitions
(kept ahead by ``ensure_future_partitions``) and a DEFAULT partition as a safety
net; retention detaches and drops whole expired months.

A partitioned table's unique constraints must contain the partition key, so
``uq_goal_events_sequence`` becomes (tenant_id, goal_id, sequence, created_at).
That constraint used to be the only thing keeping sequences unique: numbers were
``MAX(sequence) + 1``, which races under READ COMMITTED (two writers read the
same MAX) and relied on the unique violation + retry — and on a partitioned
table that MAX would also probe every partition on every append. Sequences now
come from ``goals.event_seq`` via ``UPDATE ... RETURNING``: the row lock
serialises allocation per goal, so numbers are unique by construction and the
append is O(1).

Data is copied, row counts are verified, and only then is the old table
dropped. The copy runs with FORCE ROW LEVEL SECURITY lifted on the old table:
a non-superuser owner is otherwise filtered by the tenant policy (no GUC set)
and would copy zero rows. Large installations should run this in a
maintenance window (the copy is a full rewrite of goal_events).

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
"""

# ruff: noqa: E501  (SQL statements are kept on one line each)
from __future__ import annotations

from datetime import UTC, datetime

from alembic import op
from sqlalchemy import text

revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None

_MONTHS_AHEAD = 6
_LEGACY = "goal_events_unpartitioned"
_POLICY = (
    "CREATE POLICY goal_events_tenant_isolation ON goal_events "
    "USING (tenant_id = current_setting('app.tenant_id', true)) "
    "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
)


def _months(first: datetime, last: datetime) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    yr, mo = first.year, first.month
    while (yr, mo) <= (last.year, last.month):
        out.append((yr, mo))
        yr, mo = (yr + 1, 1) if mo == 12 else (yr, mo + 1)
    return out


def _add_months(dt: datetime, n: int) -> datetime:
    total = dt.year * 12 + (dt.month - 1) + n
    return dt.replace(year=total // 12, month=total % 12 + 1, day=1)


def _rename_legacy_objects(bind: object) -> None:
    """Free the constraint/index names for the new parent table."""
    for (name,) in bind.execute(  # type: ignore[attr-defined]
        text("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass)"),
        {"t": _LEGACY},
    ).fetchall():
        op.execute(f'ALTER TABLE {_LEGACY} RENAME CONSTRAINT "{name}" TO "{name}_old"')
    for (name,) in bind.execute(  # type: ignore[attr-defined]
        text(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = :t "
            "AND indexname NOT LIKE '%\\_old' ESCAPE '\\'"
        ),
        {"t": _LEGACY},
    ).fetchall():
        op.execute(f'ALTER INDEX "{name}" RENAME TO "{name}_old"')


def upgrade() -> None:
    bind = op.get_bind()
    already = bind.execute(
        text("SELECT relkind::text FROM pg_class WHERE relname = 'goal_events' AND relnamespace = 'public'::regnamespace")
    ).scalar()
    if already == "p":
        return

    # 1. Per-goal sequence counter, seeded from the existing events.
    op.execute("ALTER TABLE goals ADD COLUMN IF NOT EXISTS event_seq INTEGER NOT NULL DEFAULT 0")
    goals_forced = bind.execute(
        text("SELECT relforcerowsecurity FROM pg_class WHERE oid = 'goals'::regclass")
    ).scalar()
    op.execute("ALTER TABLE goal_events NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE goals NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE goals g SET event_seq = s.max_seq FROM "
        "(SELECT goal_id, MAX(sequence) AS max_seq FROM goal_events GROUP BY goal_id) s "
        "WHERE g.id = s.goal_id"
    )
    if goals_forced:
        op.execute("ALTER TABLE goals FORCE ROW LEVEL SECURITY")

    # 2. Move the old table aside, keeping its grants to re-apply.
    grants = bind.execute(
        text(
            "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_schema = 'public' AND table_name = 'goal_events' "
            "AND grantee <> (SELECT tableowner FROM pg_tables WHERE schemaname = 'public' AND tablename = 'goal_events')"
        )
    ).fetchall()
    op.execute(f"ALTER TABLE goal_events RENAME TO {_LEGACY}")
    _rename_legacy_objects(bind)

    # 3. The partitioned parent.
    op.execute(
        """
        CREATE TABLE goal_events (
            id          VARCHAR(32) NOT NULL,
            tenant_id   VARCHAR(32) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            goal_id     VARCHAR(32) NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            sequence    INTEGER NOT NULL,
            event_type  VARCHAR(80) NOT NULL,
            payload     JSON NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT goal_events_pkey PRIMARY KEY (id, created_at),
            CONSTRAINT uq_goal_events_sequence UNIQUE (tenant_id, goal_id, sequence, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute("CREATE INDEX ix_goal_events_tenant_id ON goal_events (tenant_id)")
    op.execute("CREATE INDEX ix_goal_events_goal_id ON goal_events (goal_id)")
    # Replay / SSE resume read one goal's events in sequence order.
    op.execute("CREATE INDEX ix_goal_events_goal_seq ON goal_events (goal_id, sequence)")
    op.execute("CREATE TABLE goal_events_default PARTITION OF goal_events DEFAULT")

    now = datetime.now(UTC)
    oldest = bind.execute(text(f"SELECT MIN(created_at) FROM {_LEGACY}")).scalar() or now
    partitions = ["goal_events_default"]
    for yr, mo in _months(oldest, _add_months(now, _MONTHS_AHEAD)):
        start = datetime(yr, mo, 1, tzinfo=UTC)
        end = _add_months(start, 1)
        name = f"goal_events_{yr}_{mo:02d}"
        op.execute(
            f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF goal_events "
            f"FOR VALUES FROM ('{start:%Y-%m-%d}') TO ('{end:%Y-%m-%d}')"
        )
        partitions.append(name)

    # 4. Copy, verify, drop.
    op.execute(
        f"INSERT INTO goal_events (id, tenant_id, goal_id, sequence, event_type, payload, created_at) "
        f"SELECT id, tenant_id, goal_id, sequence, event_type, payload, created_at FROM {_LEGACY}"
    )
    old_n = bind.execute(text(f"SELECT COUNT(*) FROM {_LEGACY}")).scalar()
    new_n = bind.execute(text("SELECT COUNT(*) FROM goal_events")).scalar()
    if old_n != new_n:
        raise RuntimeError(f"goal_events copy mismatch: {old_n} rows before, {new_n} after")
    op.execute(f"DROP TABLE {_LEGACY}")

    # 5. Tenant isolation on the parent and on every partition (partitions do
    #    not inherit RLS; app_apply_parent_rls is from migration b4c5d6e7f8a9).
    op.execute("ALTER TABLE goal_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE goal_events FORCE ROW LEVEL SECURITY")
    op.execute(_POLICY)
    for name in partitions:
        op.execute(f"SELECT app_apply_parent_rls(CAST('{name}' AS regclass))")
    for grantee, privilege in grants:
        op.execute(f'GRANT {privilege} ON goal_events TO "{grantee}"')


def downgrade() -> None:
    bind = op.get_bind()
    kind = bind.execute(
        text("SELECT relkind::text FROM pg_class WHERE relname = 'goal_events' AND relnamespace = 'public'::regnamespace")
    ).scalar()
    if kind != "p":
        return
    op.execute("ALTER TABLE goal_events RENAME TO goal_events_partitioned")
    op.execute("ALTER TABLE goal_events_partitioned NO FORCE ROW LEVEL SECURITY")
    for (name,) in bind.execute(
        text("SELECT conname FROM pg_constraint WHERE conrelid = 'goal_events_partitioned'::regclass")
    ).fetchall():
        op.execute(f'ALTER TABLE goal_events_partitioned RENAME CONSTRAINT "{name}" TO "{name}_p"')
    for idx in ("ix_goal_events_tenant_id", "ix_goal_events_goal_id", "ix_goal_events_goal_seq"):
        op.execute(f'ALTER INDEX IF EXISTS "{idx}" RENAME TO "{idx}_p"')
    op.execute(
        """
        CREATE TABLE goal_events (
            id          VARCHAR(32) PRIMARY KEY,
            tenant_id   VARCHAR(32) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            goal_id     VARCHAR(32) NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            sequence    INTEGER NOT NULL,
            event_type  VARCHAR(80) NOT NULL,
            payload     JSON NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_goal_events_sequence UNIQUE (tenant_id, goal_id, sequence)
        )
        """
    )
    op.execute("CREATE INDEX ix_goal_events_tenant_id ON goal_events (tenant_id)")
    op.execute("CREATE INDEX ix_goal_events_goal_id ON goal_events (goal_id)")
    op.execute("INSERT INTO goal_events SELECT * FROM goal_events_partitioned")
    op.execute("DROP TABLE goal_events_partitioned CASCADE")
    op.execute("ALTER TABLE goal_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE goal_events FORCE ROW LEVEL SECURITY")
    op.execute(_POLICY)
    op.execute("ALTER TABLE goals DROP COLUMN IF EXISTS event_seq")
