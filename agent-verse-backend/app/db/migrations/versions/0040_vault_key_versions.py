# app/db/migrations/versions/0040_vault_key_versions.py
"""Vault key versions table for P2.7 key rotation + BYOK."""

revision = "0040"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op
    op.execute("""
        CREATE TABLE IF NOT EXISTS vault_key_versions (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL DEFAULT 'global',
            key_hash        TEXT NOT NULL,
            algorithm       TEXT NOT NULL DEFAULT 'AES-256-GCM',
            activated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            retired_at      TIMESTAMPTZ,
            is_current      BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_vault_key_tenant ON vault_key_versions (tenant_id, is_current)")


def downgrade() -> None:
    from alembic import op
    op.execute("DROP INDEX IF EXISTS ix_vault_key_tenant")
    op.execute("DROP TABLE IF EXISTS vault_key_versions")
