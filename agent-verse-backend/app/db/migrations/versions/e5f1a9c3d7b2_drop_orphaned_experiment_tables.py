"""Drop the orphaned tables ab_test_results, learning_experiments, learning_experiment_outcomes.

Their only readers/writers were deleted: the uncalled ``ABTestingEngine`` that wrote
``ab_test_results`` (a05-F089-01) and the governed-learning experiment store behind
``learning_experiments`` / ``learning_experiment_outcomes`` (a05-F092-02). Nothing
reads or writes them any more; the owner decided to drop them.

No data is lost silently: ``upgrade`` refuses (and changes nothing) while any of the
three tables still holds a row, unless ``AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP=1`` is set.
The rows are counted with FORCE ROW LEVEL SECURITY lifted inside this migration's
transaction, so a migration role that owns the tables but is not a superuser or
BYPASSRLS still sees every tenant's rows (with FORCE on, it would see none and the
guard would pass on a non-empty table).

``downgrade`` recreates the three tables exactly as they were at the previous head
(column widths after e7b1c4d9a2f6, the indexes, unique and foreign keys, ENABLE +
FORCE row level security and the tenant isolation policies). The app role's grants
come back from ``ensure_app_role``, which ``env.py`` runs after every migration.

Revision ID: e5f1a9c3d7b2
Revises: a7c9e1f3b5d7
Create Date: 2026-10-07
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f1a9c3d7b2"
down_revision: str | Sequence[str] | None = "a7c9e1f3b5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALLOW_ENV = "AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP"

# Drop order: learning_experiment_outcomes references learning_experiments.
ORPHAN_TABLES: tuple[str, ...] = (
    "learning_experiment_outcomes",
    "learning_experiments",
    "ab_test_results",
)

_GUC = "current_setting('app.tenant_id', true)"


class OrphanTableNotEmptyError(RuntimeError):
    """An orphaned table still holds rows and the drop was not explicitly allowed."""


def _drop_allowed() -> bool:
    return os.environ.get(ALLOW_ENV, "").strip().lower() in {"1", "true", "yes"}


def _row_counts(bind: sa.engine.Connection) -> dict[str, int]:
    """Row count of every orphaned table that still exists, across all tenants."""
    counts: dict[str, int] = {}
    for table in ORPHAN_TABLES:
        exists = bind.execute(sa.text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table})
        if not exists.scalar_one():
            continue
        # FORCE RLS binds the table owner too; lift it (rolled back with the
        # transaction if the guard refuses) so the count is not tenant-filtered.
        bind.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        counts[table] = int(bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
    return counts


def upgrade() -> None:
    bind = op.get_bind()
    counts = _row_counts(bind)
    non_empty = {table: n for table, n in counts.items() if n > 0}
    if non_empty and not _drop_allowed():
        listing = ", ".join(f"{table} ({n} rows)" for table, n in non_empty.items())
        raise OrphanTableNotEmptyError(
            f"Refusing to drop orphaned tables that still hold data: {listing}. "
            "No code reads or writes them any more, but dropping them deletes those "
            "rows permanently. Back them up if you need them, then re-run the "
            f"migration with {ALLOW_ENV}=1 to drop them anyway."
        )
    for table in counts:
        op.execute(f"DROP TABLE IF EXISTS {table}")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        f"USING (tenant_id = {_GUC}) WITH CHECK (tenant_id = {_GUC})"
    )


def downgrade() -> None:
    # As created by 0104_memory_learning, widened by e7b1c4d9a2f6.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS learning_experiments (
            id          VARCHAR(64) NOT NULL,
            tenant_id   VARCHAR(64) NOT NULL,
            agent_id    VARCHAR(64) NOT NULL,
            kind        VARCHAR(20) NOT NULL,
            target_key  TEXT NOT NULL,
            spec        JSONB NOT NULL,
            status      VARCHAR(20) NOT NULL,
            kill_switch BOOLEAN DEFAULT false NOT NULL,
            created_at  TIMESTAMPTZ DEFAULT now() NOT NULL,
            updated_at  TIMESTAMPTZ DEFAULT now() NOT NULL,
            CONSTRAINT learning_experiments_pkey PRIMARY KEY (id),
            CONSTRAINT learning_experiments_tenant_id_fkey FOREIGN KEY (tenant_id)
                REFERENCES tenants(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_learning_experiments_tenant_status "
        "ON learning_experiments (tenant_id, status, updated_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS learning_experiment_outcomes (
            id            VARCHAR(64) NOT NULL,
            tenant_id     VARCHAR(64) NOT NULL,
            experiment_id VARCHAR(64) NOT NULL,
            assignment_id VARCHAR(64) NOT NULL,
            arm           VARCHAR(20) NOT NULL,
            metrics       JSONB NOT NULL,
            recorded_at   TIMESTAMPTZ NOT NULL,
            CONSTRAINT learning_experiment_outcomes_pkey PRIMARY KEY (id),
            CONSTRAINT uq_experiment_assignment_outcome UNIQUE (tenant_id, assignment_id),
            CONSTRAINT learning_experiment_outcomes_experiment_id_fkey
                FOREIGN KEY (experiment_id) REFERENCES learning_experiments(id)
                ON DELETE CASCADE,
            CONSTRAINT learning_experiment_outcomes_tenant_id_fkey FOREIGN KEY (tenant_id)
                REFERENCES tenants(id) ON DELETE CASCADE
        )
        """
    )
    # As created by 0087_add_orchestration_tables, widened by e7b1c4d9a2f6, RLS from
    # b4c5d6e7f8a9.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ab_test_results (
            id              VARCHAR(64) NOT NULL,
            goal_id         VARCHAR(64) NOT NULL,
            tenant_id       VARCHAR(64) NOT NULL,
            experiment_type VARCHAR(100) NOT NULL,
            arm_id          VARCHAR(100) NOT NULL,
            score           DOUBLE PRECISION NOT NULL,
            created_at      TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT ab_test_results_pkey PRIMARY KEY (id),
            CONSTRAINT ab_test_results_tenant_id_fkey FOREIGN KEY (tenant_id)
                REFERENCES tenants(id) ON DELETE CASCADE
        )
        """
    )
    for column in ("experiment_type", "goal_id", "tenant_id"):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_ab_test_results_{column} "
            f"ON ab_test_results ({column})"
        )
    for table in ("learning_experiments", "learning_experiment_outcomes", "ab_test_results"):
        _rls(table)
