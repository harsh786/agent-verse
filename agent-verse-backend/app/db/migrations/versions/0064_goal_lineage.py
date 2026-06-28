"""Create goal_lineage table for parent→child goal relationships.

Revision ID: 0064
Revises: 0063
Create Date: 2026-06-28
"""

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS goal_lineage (
            id              TEXT PRIMARY KEY,
            root_goal_id    TEXT NOT NULL,
            parent_goal_id  TEXT,
            child_goal_id   TEXT NOT NULL UNIQUE,
            parent_agent_id TEXT,
            child_agent_id  TEXT,
            civilization_id TEXT,
            spawn_reason    TEXT NOT NULL DEFAULT '',
            depth           INTEGER NOT NULL DEFAULT 0,
            spawned_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            tenant_id       TEXT NOT NULL
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_goal_lineage_root "
        "ON goal_lineage (root_goal_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_goal_lineage_tenant "
        "ON goal_lineage (tenant_id)"
    )

    op.execute("ALTER TABLE goal_lineage ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE goal_lineage FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY goal_lineage_tenant_isolation ON goal_lineage
        USING (tenant_id = current_setting('app.tenant_id', TRUE))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS goal_lineage CASCADE")
