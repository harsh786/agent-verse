"""Per-tenant vault keys (BYOK for the secret vault), stored wrapped (PROV-15).

``POST /tenants/me/vault-key`` validated a customer key and returned
``validated_not_persisted``. The key is now stored here, wrapped by the platform
vault (envelope encryption), and used for the tenant's new secrets.

Revision ID: c3d9e1f7a2b8
Revises: d7e3a1f9b2c4
"""

from __future__ import annotations

from alembic import op

revision = "c3d9e1f7a2b8"
down_revision = "d7e3a1f9b2c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_vault_keys (
            tenant_id    VARCHAR(64) PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            wrapped_key  TEXT NOT NULL,
            fingerprint  VARCHAR(32) NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("ALTER TABLE tenant_vault_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_vault_keys FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_vault_keys_tenant_isolation ON tenant_vault_keys")
    op.execute(
        "CREATE POLICY tenant_vault_keys_tenant_isolation ON tenant_vault_keys "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenant_vault_keys")
