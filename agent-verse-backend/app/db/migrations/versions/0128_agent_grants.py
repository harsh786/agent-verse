"""agent_grants — persistent Grantex tool-grant store (governance).

Persists scoped/time-limited/revocable grants so tool-execution enforcement
survives restarts and can be administered via the issuance API. Tenant-isolated
with FORCE RLS, matching the other tenant tables.

Revision ID: 0128_agent_grants
Revises: 0127
"""

from __future__ import annotations

from alembic import op

revision = "0128_agent_grants"
down_revision = "0127"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_grants (
            grant_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            grantor TEXT NOT NULL,
            grantee_agent_id TEXT NOT NULL,
            scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
            not_before TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            max_cost_usd DOUBLE PRECISION,
            revoked BOOLEAN NOT NULL DEFAULT FALSE,
            parent_grant_id TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_grants_tenant_agent "
        "ON agent_grants (tenant_id, grantee_agent_id)"
    )
    op.execute("ALTER TABLE agent_grants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_grants FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS agent_grants_tenant_isolation ON agent_grants")
    op.execute(
        "CREATE POLICY agent_grants_tenant_isolation ON agent_grants "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_grants_tenant_isolation ON agent_grants")
    op.execute("DROP INDEX IF EXISTS ix_agent_grants_tenant_agent")
    op.execute("DROP TABLE IF EXISTS agent_grants")
