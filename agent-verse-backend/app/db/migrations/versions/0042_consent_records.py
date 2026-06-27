# app/db/migrations/versions/0042_consent_records.py
"""Consent records + async GDPR export jobs for P2.10."""

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op
    op.execute("""
        CREATE TABLE IF NOT EXISTS consent_records (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            purpose         TEXT NOT NULL,
            legal_basis     TEXT NOT NULL DEFAULT 'legitimate_interest',
            granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at      TIMESTAMPTZ,
            version         TEXT NOT NULL DEFAULT '1.0',
            ip_address      TEXT,
            user_agent      TEXT
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_consent_tenant ON consent_records (tenant_id, granted_at DESC)")

    # Async GDPR export jobs tracking
    op.execute("""
        CREATE TABLE IF NOT EXISTS gdpr_export_jobs (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            download_url TEXT,
            error_message TEXT
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_gdpr_jobs_tenant ON gdpr_export_jobs (tenant_id, created_at DESC)")


def downgrade() -> None:
    from alembic import op
    op.execute("DROP INDEX IF EXISTS ix_gdpr_jobs_tenant")
    op.execute("DROP TABLE IF EXISTS gdpr_export_jobs")
    op.execute("DROP INDEX IF EXISTS ix_consent_tenant")
    op.execute("DROP TABLE IF EXISTS consent_records")
