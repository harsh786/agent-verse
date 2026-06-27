"""Add agent_snapshots table for persistent agent version snapshots."""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_snapshots (
            id             TEXT        PRIMARY KEY,
            tenant_id      TEXT        NOT NULL,
            agent_id       TEXT        NOT NULL,
            version        INT         NOT NULL DEFAULT 1,
            snapshot       JSONB       NOT NULL DEFAULT '{}',
            snapshotted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_snapshots_tenant_agent "
        "ON agent_snapshots (tenant_id, agent_id)"
    )
    op.execute("ALTER TABLE agent_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_snapshots FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY agent_snapshots_tenant_isolation ON agent_snapshots "
        "USING (tenant_id = current_setting('app.current_tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_snapshots_tenant_isolation ON agent_snapshots")
    op.execute("DROP INDEX IF EXISTS ix_agent_snapshots_tenant_agent")
    op.execute("DROP TABLE IF EXISTS agent_snapshots")
