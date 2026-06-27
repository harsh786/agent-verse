"""Add golden_tasks table for P2.6 eval rollout gate."""
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS golden_tasks (
            id                       TEXT PRIMARY KEY,
            eval_suite_id            TEXT NOT NULL,
            tenant_id                TEXT NOT NULL,
            goal                     TEXT NOT NULL,
            expected_output_contains TEXT NOT NULL DEFAULT '',
            expected_tool_calls      JSONB NOT NULL DEFAULT '[]',
            forbidden_tools          JSONB NOT NULL DEFAULT '[]',
            min_score                FLOAT NOT NULL DEFAULT 0.8,
            tags                     JSONB NOT NULL DEFAULT '[]',
            created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_golden_tasks_suite "
        "ON golden_tasks (eval_suite_id, tenant_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_golden_tasks_suite")
    op.execute("DROP TABLE IF EXISTS golden_tasks")
