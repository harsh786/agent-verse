"""workspace_usage: per-tenant workspace totals for the quota (NATIVE-04).

One row per tenant holding its running byte and entry totals, updated in the
same transaction as every workspace write/delete (the row lock serialises one
tenant's writes, so the totals stay exact without summing workspace_files).

Revision ID: d4f8b0e2a6c3
Revises: c3e7a9d1f5b2
"""

from __future__ import annotations

from alembic import op

revision = "d4f8b0e2a6c3"
down_revision = "c3e7a9d1f5b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_usage (
            tenant_id   VARCHAR(64) PRIMARY KEY,
            bytes_used  BIGINT      NOT NULL DEFAULT 0,
            entries     INTEGER     NOT NULL DEFAULT 0,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    # Totals for anything already in the workspace (the migration owner bypasses RLS).
    op.execute(
        "INSERT INTO workspace_usage (tenant_id, bytes_used, entries) "
        "SELECT tenant_id, COALESCE(SUM(size_bytes), 0), COUNT(*) FROM workspace_files "
        "GROUP BY tenant_id ON CONFLICT (tenant_id) DO NOTHING"
    )
    op.execute("ALTER TABLE workspace_usage ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_usage FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS workspace_usage_tenant_isolation ON workspace_usage")
    op.execute(
        "CREATE POLICY workspace_usage_tenant_isolation ON workspace_usage "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workspace_usage")
