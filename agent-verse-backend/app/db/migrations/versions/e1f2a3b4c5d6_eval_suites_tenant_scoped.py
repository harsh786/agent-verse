"""eval_suites / eval_suite_results: tenant-scoped keys and durable run status.

The eval-suite API kept suites and runs in process memory keyed by suite id
alone, shared by every tenant (see ``app/intelligence/eval_suite_store.py``).
These tables existed but were never written by it. To make them the source of
truth:

* the primary keys become ``(tenant_id, id)`` — a globally unique suite id let
  one tenant probe for (and collide with) another tenant's ids;
* ``eval_suites.description`` is stored (the API accepted and dropped it);
* ``eval_suite_results`` gains ``status`` / ``error`` / ``finished_at`` so a run
  can be recorded ``running`` up front and finished by a background worker
  instead of holding the HTTP request open for the whole suite;
* a ``(tenant_id, suite_id, run_at)`` index serves the newest-first history.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""

from __future__ import annotations

from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def _swap_pk(table: str, columns: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE pk text;
        BEGIN
            SELECT conname INTO pk FROM pg_constraint
            WHERE conrelid = '{table}'::regclass AND contype = 'p';
            IF pk IS NOT NULL THEN
                EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', pk);
            END IF;
        END $$
        """
    )
    op.execute(f"ALTER TABLE {table} ADD PRIMARY KEY ({columns})")


def upgrade() -> None:
    op.execute(
        "ALTER TABLE eval_suites ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''"
    )
    _swap_pk("eval_suites", "tenant_id, id")
    op.execute(
        "ALTER TABLE eval_suite_results "
        "ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'completed', "
        "ADD COLUMN IF NOT EXISTS error TEXT, "
        "ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ"
    )
    _swap_pk("eval_suite_results", "tenant_id, id")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eval_suite_results_tenant_suite_run "
        "ON eval_suite_results (tenant_id, suite_id, run_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_eval_suite_results_tenant_suite_run")
    _swap_pk("eval_suite_results", "id")
    op.execute(
        "ALTER TABLE eval_suite_results DROP COLUMN IF EXISTS finished_at, "
        "DROP COLUMN IF EXISTS error, DROP COLUMN IF EXISTS status"
    )
    _swap_pk("eval_suites", "id")
    op.execute("ALTER TABLE eval_suites DROP COLUMN IF EXISTS description")
