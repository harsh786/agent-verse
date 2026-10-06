"""tenant_email_settings: recipient allowlist + tenant-owned SMTP sender (a02-F036-02).

Owner decision: the agent email tool (``POST /tools/email/send``) gets (a) an
optional per-tenant recipient allowlist (empty = today's behaviour) and (d) a
tenant-owned SMTP sender as the production path; the platform relay stays for
system mail and for tenants without one. ``smtp_secret_enc`` holds only vault
ciphertext (``tv1:`` with the tenant's own key, else the platform vault) and is
rotated by ``agentverse vault-rotate`` / tenant key compaction like the tenant
LLM key. Tenant-scoped, FORCE RLS.

Revision ID: c4e6a8b0d2f4
Revises: a7c9e1f3b5d7
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c4e6a8b0d2f4"
down_revision: str | Sequence[str] | None = "a7c9e1f3b5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tenant_email_settings"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            tenant_id              VARCHAR(64) PRIMARY KEY
                                   REFERENCES tenants(id) ON DELETE CASCADE,
            recipient_allowlist    JSONB NOT NULL DEFAULT '[]'::jsonb,
            smtp_host              TEXT,
            smtp_port              INTEGER,
            smtp_tls_mode          VARCHAR(16),
            smtp_username          TEXT,
            smtp_from_address      TEXT,
            smtp_secret_enc        TEXT,
            vault_key_fingerprint  VARCHAR(64),
            updated_by             TEXT,
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_tenant_email_settings_tls_mode
                CHECK (smtp_tls_mode IS NULL OR smtp_tls_mode IN ('starttls', 'tls', 'none')),
            CONSTRAINT ck_tenant_email_settings_port
                CHECK (smtp_port IS NULL OR (smtp_port BETWEEN 1 AND 65535)),
            CONSTRAINT ck_tenant_email_settings_allowlist_array
                CHECK (jsonb_typeof(recipient_allowlist) = 'array')
        )
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.execute(
        f"CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
