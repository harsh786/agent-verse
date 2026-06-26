"""Create audit_log and approval_requests tables.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── audit_log (append-only) ───────────────────────────────────────────
    # Intentionally has no FK to tenants — audit entries must survive tenant deletion.
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("goal_id", sa.String(32), nullable=False),
        sa.Column("tool_name", sa.String(200), nullable=False),
        sa.Column("action_level", sa.String(20), nullable=False),
        sa.Column("outcome", sa.String(100), nullable=False),
        sa.Column("step_id", sa.String(32), nullable=True, server_default=""),
        sa.Column("approver", sa.String(200), nullable=True),
        sa.Column("note", sa.Text, nullable=True, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_goal_id", "audit_log", ["goal_id"])
    op.create_index("ix_audit_log_tenant_created", "audit_log", ["tenant_id", "created_at"])

    # Immutability trigger — no UPDATE or DELETE allowed
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_modification()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Audit log entries are immutable';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_immutable
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();
        """
    )

    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_log FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY audit_log_tenant_isolation ON audit_log "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ── approval_requests ─────────────────────────────────────────────────
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("goal_id", sa.String(32), nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("approver", sa.String(200), nullable=True),
        sa.Column("note", sa.Text, nullable=True, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_approval_requests_tenant_id", "approval_requests", ["tenant_id"])
    op.create_index("ix_approval_requests_goal_id", "approval_requests", ["goal_id"])
    op.create_index(
        "ix_approval_requests_tenant_status",
        "approval_requests",
        ["tenant_id", "status"],
    )

    op.execute("ALTER TABLE approval_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE approval_requests FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY approval_requests_tenant_isolation ON approval_requests "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS approval_requests_tenant_isolation ON approval_requests"
    )
    op.drop_table("approval_requests")

    op.execute("DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log")
    op.execute("DROP POLICY IF EXISTS audit_log_tenant_isolation ON audit_log")
    op.drop_table("audit_log")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_modification()")
