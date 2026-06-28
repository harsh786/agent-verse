"""tenant_settings table and gdpr_export_jobs if not exists

Revision ID: 0058
Revises: 0057
Create Date: 2026-06-28
"""
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Table: tenant_settings                                               #
    # Per-tenant configuration key/value store (JSONB)                    #
    # Used by compliance checker, self-optimizer, rate-limiter            #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS tenant_settings (
            tenant_id   TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            settings    JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tenant_settings_tenant
            ON tenant_settings(tenant_id)
    """)

    # ------------------------------------------------------------------ #
    # Table: gdpr_export_jobs — ensure it exists (may already exist       #
    # from 0042_consent_records.py; IF NOT EXISTS is idempotent)          #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS gdpr_export_jobs (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at    TIMESTAMPTZ,
            download_url    TEXT,
            error_message   TEXT
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_gdpr_jobs_tenant
            ON gdpr_export_jobs(tenant_id, created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenant_settings CASCADE")
