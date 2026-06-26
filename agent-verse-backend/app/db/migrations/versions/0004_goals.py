"""Create goals and goal_steps tables.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goals",
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
        sa.Column(
            "parent_goal_id",
            sa.String(32),
            sa.ForeignKey("goals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("goal_text", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="planning"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="normal"),
        sa.Column(
            "autonomy_mode",
            sa.String(50),
            nullable=False,
            server_default="bounded-autonomous",
        ),
        sa.Column("dry_run", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
        sa.Column("iterations", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text, nullable=True, server_default=""),
        sa.Column("verification_feedback", sa.Text, nullable=True, server_default=""),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_goals_tenant_id", "goals", ["tenant_id"])
    op.create_index("ix_goals_tenant_status", "goals", ["tenant_id", "status"])
    op.create_index("ix_goals_tenant_agent", "goals", ["tenant_id", "agent_id"])

    op.execute("ALTER TABLE goals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE goals FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY goals_tenant_isolation ON goals "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    op.create_table(
        "goal_steps",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "goal_id",
            sa.String(32),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("step_index", sa.Integer, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("output", sa.Text, nullable=True, server_default=""),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("tool_calls", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_goal_steps_goal_id", "goal_steps", ["goal_id"])
    op.create_index("ix_goal_steps_tenant_id", "goal_steps", ["tenant_id"])

    op.execute("ALTER TABLE goal_steps ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE goal_steps FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY goal_steps_tenant_isolation ON goal_steps "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS goal_steps_tenant_isolation ON goal_steps")
    op.drop_table("goal_steps")
    op.execute("DROP POLICY IF EXISTS goals_tenant_isolation ON goals")
    op.drop_table("goals")
