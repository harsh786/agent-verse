"""Add tool_reliability_memory table for per-tool success/failure tracking."""
from alembic import op

revision = "0037"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tool_reliability_memory (
            tenant_id        TEXT NOT NULL,
            tool_name        TEXT NOT NULL,
            success_count    INT NOT NULL DEFAULT 0,
            failure_count    INT NOT NULL DEFAULT 0,
            total_latency_ms FLOAT NOT NULL DEFAULT 0.0,
            last_used_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, tool_name)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tool_rel_tenant "
        "ON tool_reliability_memory (tenant_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tool_rel_tenant")
    op.execute("DROP TABLE IF EXISTS tool_reliability_memory")
