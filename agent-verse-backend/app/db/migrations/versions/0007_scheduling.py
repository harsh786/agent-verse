"""Create policies and schedules tables.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── policies ──────────────────────────────────────────────────────────
    op.create_table(
        "policies",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True, server_default=""),
        sa.Column(
            "denied_tools", sa.JSON, nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "approval_tools", sa.JSON, nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("scope", sa.String(50), nullable=True, server_default="global"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_policies_tenant_id", "policies", ["tenant_id"])

    op.execute("ALTER TABLE policies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE policies FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY policies_tenant_isolation ON policies "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ── schedules ─────────────────────────────────────────────────────────
    op.create_table(
        "schedules",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.String(32),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("goal_id_template", sa.String(500), nullable=False),
        sa.Column("trigger_type", sa.String(20), nullable=False),
        sa.Column("cron_expression", sa.String(200), nullable=True, server_default=""),
        sa.Column("timezone", sa.String(100), nullable=True, server_default="UTC"),
        sa.Column(
            "interval_seconds", sa.Integer, nullable=True, server_default=sa.text("0")
        ),
        sa.Column("webhook_token", sa.String(64), nullable=True, server_default=""),
        sa.Column("event_channel", sa.String(200), nullable=True, server_default=""),
        sa.Column("fire_at_iso", sa.String(100), nullable=True, server_default=""),
        sa.Column("condition", sa.Text, nullable=True, server_default=""),
        sa.Column("description", sa.Text, nullable=True, server_default=""),
        sa.Column("paused", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_schedules_tenant_id", "schedules", ["tenant_id"])
    op.create_index("ix_schedules_agent_id", "schedules", ["agent_id"])
    # Partial unique index: webhook_token must be unique where non-empty
    op.execute(
        "CREATE UNIQUE INDEX uq_schedules_webhook_token "
        "ON schedules (webhook_token) "
        "WHERE webhook_token != ''"
    )

    op.execute("ALTER TABLE schedules ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE schedules FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY schedules_tenant_isolation ON schedules "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS schedules_tenant_isolation ON schedules")
    op.execute("DROP INDEX IF EXISTS uq_schedules_webhook_token")
    op.drop_table("schedules")
    op.execute("DROP POLICY IF EXISTS policies_tenant_isolation ON policies")
    op.drop_table("policies")
