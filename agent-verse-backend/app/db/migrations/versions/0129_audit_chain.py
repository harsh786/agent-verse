"""audit_chain — persistent tamper-evident hash-chained audit (Grantex G5 wiring).

Persists the hash chain so tamper-evidence survives restarts and can be exported
as a compliance evidence pack. Tenant-isolated with FORCE RLS. One monotonic
sequence per tenant; each row carries the prior row's hash so any edit/insert/
delete in the middle breaks every later hash.

Revision ID: 0129_audit_chain
Revises: 0128_agent_grants
"""

from __future__ import annotations

from alembic import op

revision = "0129_audit_chain"
down_revision = "0128_agent_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_chain (
            tenant_id TEXT NOT NULL,
            seq BIGINT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            prev_hash TEXT NOT NULL,
            record_hash TEXT NOT NULL,
            PRIMARY KEY (tenant_id, seq)
        )
        """
    )
    op.execute("ALTER TABLE audit_chain ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_chain FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS audit_chain_tenant_isolation ON audit_chain")
    op.execute(
        "CREATE POLICY audit_chain_tenant_isolation ON audit_chain "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS audit_chain_tenant_isolation ON audit_chain")
    op.execute("DROP TABLE IF EXISTS audit_chain")
