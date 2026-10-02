"""goal_fanout_ledger: durable decomposition + child state for supervisor / goal_tree.

A supervisor parent (CORE-31) decomposed its goal and launched sub-goals with
only in-memory bookkeeping (``supervisor_applied`` was set after every sub-goal
finished), so a parent redelivered after a crash re-decomposed and launched
duplicate sub-goals, repeating completed side effects. Goal-tree children
(CORE-10) ran under synthetic ids with checkpointing disabled, so a redelivered
parent re-ran children whose side effects had already happened.

One row per planned child, keyed (tenant_id, parent_goal_id, kind, task_key):
the decomposition (``spec``), the child goal id once dispatched, and its final
status/result. Rows die with the parent goal (FK ON DELETE CASCADE), so goal
retention purges them too. A parent holds at most a handful of rows.

Also adds ``ix_goals_tenant_parent`` so a resumed supervisor can find the
sub-goal rows it already created (``goals.parent_goal_id`` had no index).

Revision ID: a7c3e9f1b5d2
Revises: cf87de8eae52
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a7c3e9f1b5d2"
down_revision: str | None = "cf87de8eae52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS goal_fanout_ledger (
            tenant_id      VARCHAR(32) NOT NULL,
            parent_goal_id VARCHAR(32) NOT NULL
                REFERENCES goals(id) ON DELETE CASCADE,
            kind           VARCHAR(20) NOT NULL,
            task_key       VARCHAR(64) NOT NULL,
            position       INTEGER NOT NULL DEFAULT 0,
            spec           JSONB NOT NULL DEFAULT '{}'::jsonb,
            child_goal_id  VARCHAR(255),
            status         VARCHAR(20) NOT NULL DEFAULT 'planned',
            result         TEXT NOT NULL DEFAULT '',
            error          TEXT NOT NULL DEFAULT '',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, parent_goal_id, kind, task_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_goal_fanout_ledger_parent "
        "ON goal_fanout_ledger (parent_goal_id)"
    )
    op.execute("ALTER TABLE goal_fanout_ledger ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE goal_fanout_ledger FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS goal_fanout_ledger_tenant_isolation ON goal_fanout_ledger")
    op.execute(
        "CREATE POLICY goal_fanout_ledger_tenant_isolation ON goal_fanout_ledger "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_goals_tenant_parent "
        "ON goals (tenant_id, parent_goal_id) WHERE parent_goal_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_goals_tenant_parent")
    op.execute("DROP TABLE IF EXISTS goal_fanout_ledger CASCADE")
