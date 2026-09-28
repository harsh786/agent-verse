"""Durable per-tenant LLM provider configuration (BYOK).

Tenant LLM configs lived in Redis plus a per-replica ``app.state`` dict: the goal
path read the dict, so a tenant's provider applied only on the replica that
handled the PUT, and a Redis flush dropped every tenant's key. Postgres is now
the source of truth (Redis stays as a read-through cache).

Revision ID: e8f9a0b1c2d3
Revises: f7a8b9c0d1e2
"""

from __future__ import annotations

from alembic import op

revision = "e8f9a0b1c2d3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_llm_configs (
            tenant_id      VARCHAR(64) PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            provider       VARCHAR(40) NOT NULL,
            encrypted_key  TEXT NOT NULL,
            model          VARCHAR(200) NOT NULL DEFAULT '',
            base_url       TEXT,
            masked_key     VARCHAR(40),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("ALTER TABLE tenant_llm_configs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_llm_configs FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_llm_configs_tenant_isolation ON tenant_llm_configs")
    op.execute(
        "CREATE POLICY tenant_llm_configs_tenant_isolation ON tenant_llm_configs "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenant_llm_configs")
