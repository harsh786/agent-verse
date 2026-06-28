"""Persist notification channels to PostgreSQL with RLS."""
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS notification_channels (
            channel_id  TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            channel_type TEXT NOT NULL,
            config      JSONB NOT NULL DEFAULT '{}',
            enabled     BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_channels_tenant"
        " ON notification_channels (tenant_id)"
    )
    op.execute("ALTER TABLE notification_channels ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notification_channels FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY notification_channels_tenant_isolation ON notification_channels
        USING (tenant_id = current_setting('app.tenant_id', TRUE))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notification_channels CASCADE")
