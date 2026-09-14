"""prospective_memory — persist deferred intentions/reminders.

Makes prospective memory durable (was in-memory only) so intentions survive
restarts, are recalled into the planner, and can be leased/fired by the
scheduler. Tenant-isolated with FORCE RLS. Idempotent create via a unique
(tenant_id, idempotency_key).

Revision ID: 0130_prospective_memory
Revises: 0129_audit_chain
"""

from __future__ import annotations

from alembic import op

revision = "0130_prospective_memory"
down_revision = "0129_audit_chain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS prospective_memory (
            memory_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            intention TEXT NOT NULL,
            due_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            source_goal_id TEXT NOT NULL DEFAULT '',
            source_execution_id TEXT NOT NULL DEFAULT '',
            policy_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            classification TEXT NOT NULL DEFAULT 'internal',
            idempotency_key TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            fencing_token BIGINT NOT NULL DEFAULT 0,
            lease_expires_at TIMESTAMPTZ,
            result JSONB
        )
        """
    )
    op.execute(
        "ALTER TABLE prospective_memory "
        "ADD CONSTRAINT uq_prospective_mem_idem UNIQUE (tenant_id, idempotency_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_prospective_due "
        "ON prospective_memory (tenant_id, due_at) "
        "WHERE state IN ('pending', 'leased')"
    )
    op.execute("ALTER TABLE prospective_memory ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE prospective_memory FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS prospective_memory_tenant_isolation ON prospective_memory")
    op.execute(
        "CREATE POLICY prospective_memory_tenant_isolation ON prospective_memory "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS prospective_memory_tenant_isolation ON prospective_memory")
    op.execute("DROP INDEX IF EXISTS ix_prospective_due")
    op.execute("DROP TABLE IF EXISTS prospective_memory")
