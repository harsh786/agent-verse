"""0106 — trigger events, DLQ, and audit tables.

Revision ID: 0106
Revises: 0105_add_chat_tables
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── trigger_events (immutable append-only log) ────────────────────────────
    op.create_table(
        "trigger_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("trigger_id", sa.String(36), nullable=False, index=True),
        sa.Column("trigger_type", sa.Text, nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("fired_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("goal_created", sa.Boolean, nullable=False, server_default="FALSE"),
        sa.Column("goal_id", sa.String(36), nullable=True),
        sa.Column("skip_reason", sa.Text, nullable=True),
        sa.Column("processing_ms", sa.Integer, nullable=True),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_trigger_event_idempotency"),
    )
    op.create_index(
        "idx_trigger_events_tenant_fired",
        "trigger_events",
        ["tenant_id", "fired_at"],
    )
    # RLS
    op.execute("ALTER TABLE trigger_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY trigger_events_tenant ON trigger_events "
        "USING (tenant_id = current_setting('app.tenant_id', true)::text)"
    )

    # ── trigger_dlq ───────────────────────────────────────────────────────────
    op.create_table(
        "trigger_dlq",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("trigger_id", sa.String(36), nullable=False),
        sa.Column("failed_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
        sa.Column("failure_type", sa.Text, nullable=False),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("raw_payload", sa.JSON, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
        sa.Column("resolved_by", sa.Text, nullable=True),
    )
    op.execute("ALTER TABLE trigger_dlq ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY trigger_dlq_tenant ON trigger_dlq "
        "USING (tenant_id = current_setting('app.tenant_id', true)::text)"
    )

    # ── trigger_audit_events ──────────────────────────────────────────────────
    op.create_table(
        "trigger_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("trigger_id", sa.String(36), nullable=True),
        sa.Column("actor_id", sa.Text, nullable=False),
        sa.Column("actor_role", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("before_state", sa.JSON, nullable=True),
        sa.Column("after_state", sa.JSON, nullable=True),
        sa.Column("occurred_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
        sa.Column("ip_address", sa.Text, nullable=True),
        sa.Column("request_id", sa.Text, nullable=True),
    )
    op.execute("ALTER TABLE trigger_audit_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY trigger_audit_tenant ON trigger_audit_events "
        "USING (tenant_id = current_setting('app.tenant_id', true)::text)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS trigger_audit_tenant ON trigger_audit_events")
    op.execute("DROP POLICY IF EXISTS trigger_dlq_tenant ON trigger_dlq")
    op.execute("DROP POLICY IF EXISTS trigger_events_tenant ON trigger_events")
    op.drop_table("trigger_audit_events")
    op.drop_table("trigger_dlq")
    op.drop_table("trigger_events")
