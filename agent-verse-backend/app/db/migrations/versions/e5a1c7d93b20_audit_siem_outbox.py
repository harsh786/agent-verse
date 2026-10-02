"""Durable SIEM outbox for audit events (AUDIT-06).

SIEM forwarding was a per-process deque in the API only: a failed batch was
dropped, a restart lost the buffer, and worker audit events never reached the
SIEM. Each ``audit_log`` INSERT now also inserts a row here in the same
transaction (transactional outbox); one beat consumer drains it with
``FOR UPDATE SKIP LOCKED``, deletes sent rows, retries failures with backoff and
parks rows that keep failing as ``dead`` (DLQ).

Tenant RLS (rows are written by the least-privilege app role inside the
tenant's context); the drainer runs as the BYPASSRLS maintenance role.

Revision ID: e5a1c7d93b20
Revises: cf87de8eae52
"""

from __future__ import annotations

from alembic import op

revision = "e5a1c7d93b20"
down_revision = "cf87de8eae52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_siem_outbox (
            id               BIGSERIAL PRIMARY KEY,
            tenant_id        TEXT NOT NULL,
            audit_id         TEXT NOT NULL,
            payload          JSONB NOT NULL,
            status           TEXT NOT NULL DEFAULT 'pending',
            attempts         INTEGER NOT NULL DEFAULT 0,
            next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_error       TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT audit_siem_outbox_status_chk CHECK (status IN ('pending', 'dead'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_siem_outbox_due "
        "ON audit_siem_outbox (next_attempt_at, id) WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_siem_outbox_tenant "
        "ON audit_siem_outbox (tenant_id, status)"
    )
    op.execute("ALTER TABLE audit_siem_outbox ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_siem_outbox FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS audit_siem_outbox_tenant_isolation ON audit_siem_outbox")
    op.execute(
        "CREATE POLICY audit_siem_outbox_tenant_isolation ON audit_siem_outbox "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_siem_outbox")
