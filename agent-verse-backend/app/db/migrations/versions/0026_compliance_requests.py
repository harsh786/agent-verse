"""Add compliance_requests and deleted_tenants tables for durable GDPR state."""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS compliance_requests (
            request_id   TEXT        PRIMARY KEY,
            tenant_id    TEXT        NOT NULL,
            status       TEXT        NOT NULL DEFAULT 'pending',
            download_url TEXT        NOT NULL DEFAULT '',
            payload      JSONB       NOT NULL DEFAULT '{}',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_compliance_requests_tenant "
        "ON compliance_requests (tenant_id)"
    )
    op.execute("ALTER TABLE compliance_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE compliance_requests FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY compliance_requests_tenant_isolation ON compliance_requests "
        "USING (tenant_id = current_setting('app.current_tenant_id', TRUE))"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS deleted_tenants (
            tenant_id    TEXT        PRIMARY KEY,
            requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS deleted_tenants")
    op.execute(
        "DROP POLICY IF EXISTS compliance_requests_tenant_isolation ON compliance_requests"
    )
    op.execute("DROP INDEX IF EXISTS ix_compliance_requests_tenant")
    op.execute("DROP TABLE IF EXISTS compliance_requests")
