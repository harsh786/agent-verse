"""Create decision_traces table for persistent explainability traces."""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS decision_traces (
            id           TEXT        PRIMARY KEY,
            goal_id      TEXT        NOT NULL,
            tenant_id    TEXT        NOT NULL,
            action       TEXT        NOT NULL DEFAULT '',
            reasoning    TEXT        NOT NULL DEFAULT '',
            evidence     JSONB       NOT NULL DEFAULT '[]',
            alternatives JSONB       NOT NULL DEFAULT '[]',
            confidence   FLOAT       NOT NULL DEFAULT 0.8,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_decision_traces_goal_id ON decision_traces (goal_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_decision_traces_tenant_id ON decision_traces (tenant_id)")
    op.execute("ALTER TABLE decision_traces ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE decision_traces FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS decision_traces_tenant_isolation ON decision_traces")
    op.execute(
        "CREATE POLICY decision_traces_tenant_isolation ON decision_traces "
        "USING (tenant_id = current_setting('app.current_tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS decision_traces_tenant_isolation ON decision_traces")
    op.execute("DROP INDEX IF EXISTS ix_decision_traces_tenant_id")
    op.execute("DROP INDEX IF EXISTS ix_decision_traces_goal_id")
    op.execute("DROP TABLE IF EXISTS decision_traces")
