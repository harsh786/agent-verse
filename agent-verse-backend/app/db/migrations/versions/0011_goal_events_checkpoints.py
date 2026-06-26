"""Add goal event store and checkpoint metadata.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goal_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "goal_id",
            sa.String(32),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("tenant_id", "goal_id", "sequence", name="uq_goal_events_sequence"),
    )
    op.create_index("ix_goal_events_tenant_id", "goal_events", ["tenant_id"])
    op.create_index("ix_goal_events_goal_id", "goal_events", ["goal_id"])

    op.execute("ALTER TABLE goal_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE goal_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY goal_events_tenant_isolation ON goal_events "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    op.create_table(
        "goal_checkpoints",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "goal_id",
            sa.String(32),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checkpoint_key", sa.String(120), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False, server_default="0"),
        sa.Column("payload", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("checkpoint_metadata", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "recovery_status",
            sa.String(40),
            nullable=False,
            server_default="not_implemented",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "goal_id", "checkpoint_key", name="uq_goal_checkpoints_key"
        ),
    )
    op.create_index("ix_goal_checkpoints_tenant_id", "goal_checkpoints", ["tenant_id"])
    op.create_index("ix_goal_checkpoints_goal_id", "goal_checkpoints", ["goal_id"])

    op.execute("ALTER TABLE goal_checkpoints ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE goal_checkpoints FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY goal_checkpoints_tenant_isolation ON goal_checkpoints "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS goal_checkpoints_tenant_isolation ON goal_checkpoints")
    op.drop_table("goal_checkpoints")
    op.execute("DROP POLICY IF EXISTS goal_events_tenant_isolation ON goal_events")
    op.drop_table("goal_events")
