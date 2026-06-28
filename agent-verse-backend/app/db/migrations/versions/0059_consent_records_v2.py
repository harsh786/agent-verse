"""consent_records v2, scim provisioning prep

Revision ID: 0059
Revises: 0058
Create Date: 2026-06-28
"""
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Ensure consent_records exists (idempotent — may exist from 0042)    #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS consent_records (
            id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id   TEXT NOT NULL,
            purpose     TEXT NOT NULL,
            legal_basis TEXT NOT NULL DEFAULT 'legitimate_interest',
            granted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at  TIMESTAMPTZ,
            ip_address  TEXT,
            user_agent  TEXT
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_consent_records_tenant
            ON consent_records(tenant_id, purpose)
    """)


def downgrade() -> None:
    pass  # consent_records pre-existed — do not drop
