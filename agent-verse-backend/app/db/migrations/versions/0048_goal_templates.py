"""Create goal_templates table with RLS."""
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS goal_templates (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            goal_text   TEXT NOT NULL,
            parameters  JSONB NOT NULL DEFAULT '[]',
            domain      TEXT NOT NULL DEFAULT 'general',
            use_count   INTEGER NOT NULL DEFAULT 0,
            version     INTEGER NOT NULL DEFAULT 1,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_goal_templates_tenant ON goal_templates (tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_goal_templates_domain ON goal_templates (domain)")
    op.execute("ALTER TABLE goal_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE goal_templates FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY goal_templates_tenant_isolation ON goal_templates
        USING (tenant_id = current_setting('app.tenant_id', TRUE))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS goal_templates CASCADE")
