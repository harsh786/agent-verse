"""Drop the never-read golden_datasets / golden_dataset_items tables (a10-F235-01)

``/eval/golden-datasets`` was a 501 stub over these two tables (migration 0076)
and nothing ever read or wrote them. Golden datasets are the versioned
``golden_tasks`` of eval suites (MEM-54); a goal is promoted into one with
``POST /intelligence/eval-suites/{id}/tasks/from-goal/{goal_id}``. The stub
route is removed and the tables are dropped.

No data is lost silently: ``upgrade`` refuses (changing nothing) while either
table holds a row, unless ``AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP=1`` is set — the
same opt-in as e5f1a9c3d7b2. Rows are counted with FORCE ROW LEVEL SECURITY
lifted inside this transaction, so an owner that is not a superuser/BYPASSRLS
still sees every tenant's rows.

``downgrade`` recreates both tables as they were (0076, ids widened by
e7b1c4d9a2f6, ENABLE + FORCE RLS, tenant policy).

Revision ID: f7d9b1c3e5a8
Revises: e6c8a0b2d4f7
Create Date: 2026-10-07
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7d9b1c3e5a8"
down_revision: str | Sequence[str] | None = "e6c8a0b2d4f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALLOW_ENV = "AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP"
# Drop order: items reference datasets.
UNUSED_TABLES: tuple[str, ...] = ("golden_dataset_items", "golden_datasets")


class UnusedTableNotEmptyError(RuntimeError):
    """An unused table still holds rows and the drop was not explicitly allowed."""


def _drop_allowed() -> bool:
    return os.environ.get(ALLOW_ENV, "").strip().lower() in {"1", "true", "yes"}


def _row_counts(bind: sa.engine.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in UNUSED_TABLES:
        exists = bind.execute(sa.text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table})
        if not exists.scalar_one():
            continue
        # FORCE RLS binds the owner too; lifted for the count (rolled back with
        # the transaction when the guard refuses).
        bind.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        counts[table] = int(bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
    return counts


def upgrade() -> None:
    counts = _row_counts(op.get_bind())
    non_empty = {t: n for t, n in counts.items() if n > 0}
    if non_empty and not _drop_allowed():
        listing = ", ".join(f"{t} ({n} rows)" for t, n in non_empty.items())
        raise UnusedTableNotEmptyError(
            f"Refusing to drop unused tables that still hold data: {listing}. No code "
            "reads or writes them, but dropping them deletes those rows permanently. "
            f"Back them up if you need them, then re-run with {ALLOW_ENV}=1."
        )
    for table in counts:
        op.execute(f"DROP TABLE IF EXISTS {table}")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS golden_datasets (
            id          VARCHAR(64) PRIMARY KEY,
            tenant_id   VARCHAR(64) NOT NULL,
            name        VARCHAR(128) NOT NULL,
            domain      VARCHAR(64),
            version     INTEGER DEFAULT 1 NOT NULL,
            split       VARCHAR(32) DEFAULT 'regression' NOT NULL,
            description TEXT,
            item_count  INTEGER DEFAULT 0 NOT NULL,
            created_at  TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_golden_datasets_tenant ON golden_datasets (tenant_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS golden_dataset_items (
            id              VARCHAR(64) PRIMARY KEY,
            dataset_id      VARCHAR(64) REFERENCES golden_datasets (id) ON DELETE CASCADE,
            tenant_id       VARCHAR(64) NOT NULL,
            goal            TEXT NOT NULL,
            expected_output TEXT,
            human_label     BOOLEAN,
            metadata        JSON DEFAULT '{}' NOT NULL,
            created_at      TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_golden_items_dataset ON golden_dataset_items (dataset_id)"
    )
    for table in ("golden_datasets", "golden_dataset_items"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
        )
