"""Add immutability trigger to audit_log and legal hold support.

Revision ID: 0031
Revises: 0030
"""
from __future__ import annotations

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create function that prevents DELETE/UPDATE on audit_log
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_modification()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log records are immutable — modification forbidden (GDPR/SOC2 compliance)';
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Apply trigger to audit_log table
    op.execute("""
        CREATE TRIGGER audit_log_immutability
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_modification();
    """)

    # Legal hold table — prevents retention policy from deleting held data
    op.execute("""
        CREATE TABLE IF NOT EXISTS legal_holds (
            id          TEXT        PRIMARY KEY,
            tenant_id   TEXT        NOT NULL,
            reason      TEXT        NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMPTZ,
            created_by  TEXT        NOT NULL DEFAULT ''
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_legal_holds_tenant ON legal_holds (tenant_id)")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_immutability ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_modification()")
    op.execute("DROP TABLE IF EXISTS legal_holds")
