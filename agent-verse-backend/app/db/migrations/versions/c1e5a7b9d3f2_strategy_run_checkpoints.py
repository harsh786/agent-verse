"""strategy_run_checkpoints: durable StrategyRunner checkpoints (CORE-18).

Distributed supervisor / goal_tree / debate / voyager runs checkpointed into a
per-process LRU, so a worker crash or redelivery restarted the run and repeated
its LLM spend and side effects. One row per (tenant, goal, strategy): the
adapter's latest typed state and the sub-task answers it references. Rows
cascade with the goal, so goal retention purges them.

Revision ID: c1e5a7b9d3f2
Revises: e2e2811317df
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c1e5a7b9d3f2"
down_revision: str | None = "e2e2811317df"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_run_checkpoints (
            tenant_id    VARCHAR(32) NOT NULL,
            goal_id      VARCHAR(32) NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            strategy_id  VARCHAR(100) NOT NULL,
            session_id   TEXT NOT NULL DEFAULT '',
            execution_id TEXT NOT NULL DEFAULT '',
            state_type   VARCHAR(64) NOT NULL DEFAULT '',
            state        JSONB NOT NULL DEFAULT '{}'::jsonb,
            answers      JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, goal_id, strategy_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_strategy_run_checkpoints_goal "
        "ON strategy_run_checkpoints (goal_id)"
    )
    op.execute("ALTER TABLE strategy_run_checkpoints ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE strategy_run_checkpoints FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS strategy_run_checkpoints_tenant_isolation "
        "ON strategy_run_checkpoints"
    )
    op.execute(
        "CREATE POLICY strategy_run_checkpoints_tenant_isolation ON strategy_run_checkpoints "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS strategy_run_checkpoints CASCADE")
