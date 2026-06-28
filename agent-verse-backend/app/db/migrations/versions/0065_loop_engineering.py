"""Add goal_attempts and goal_step_loops tables for loop engineering persistence.

Revision ID: 0065
Revises: 0064
Create Date: 2026-06-28
"""

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Goal-level attempt history
    op.execute("""
        CREATE TABLE IF NOT EXISTS goal_attempts (
            id              TEXT PRIMARY KEY,
            goal_id         TEXT NOT NULL,
            tenant_id       TEXT NOT NULL,
            attempt_number  INTEGER NOT NULL,
            strategy        TEXT NOT NULL,
            enriched_goal   TEXT NOT NULL DEFAULT '',
            started_at      TIMESTAMPTZ NOT NULL,
            ended_at        TIMESTAMPTZ,
            succeeded       BOOLEAN,
            failure_reason  TEXT,
            iterations_used INTEGER,
            cost_usd        NUMERIC(10, 6),
            backoff_seconds INTEGER NOT NULL DEFAULT 0
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_goal_attempts_goal "
        "ON goal_attempts (goal_id, attempt_number)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_goal_attempts_tenant "
        "ON goal_attempts (tenant_id)"
    )

    # Step-level loop_until iteration history
    op.execute("""
        CREATE TABLE IF NOT EXISTS goal_step_loops (
            id               TEXT PRIMARY KEY,
            goal_id          TEXT NOT NULL,
            tenant_id        TEXT NOT NULL,
            step_index       INTEGER NOT NULL,
            step_description TEXT NOT NULL DEFAULT '',
            loop_condition   TEXT NOT NULL DEFAULT '',
            iteration_number INTEGER NOT NULL,
            condition_result BOOLEAN,
            output_snapshot  TEXT NOT NULL DEFAULT '',
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_goal_step_loops_goal "
        "ON goal_step_loops (goal_id, step_index)"
    )

    for table in ["goal_attempts", "goal_step_loops"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.tenant_id', TRUE))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
        """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS goal_step_loops CASCADE")
    op.execute("DROP TABLE IF EXISTS goal_attempts CASCADE")
