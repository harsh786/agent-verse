"""Create workflows table with tenant isolation, RLS, and indexes."""
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflows (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            definition  JSONB NOT NULL DEFAULT '{}',
            status      TEXT NOT NULL DEFAULT 'draft',
            version     INTEGER NOT NULL DEFAULT 1,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflows_tenant_id ON workflows (tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflows_status ON workflows (status)"
    )

    # Row-Level Security — same pattern as all other tenant-scoped tables.
    op.execute("ALTER TABLE workflows ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflows FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY workflows_tenant_isolation ON workflows
        USING (tenant_id = current_setting('app.tenant_id', TRUE))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workflows CASCADE")
