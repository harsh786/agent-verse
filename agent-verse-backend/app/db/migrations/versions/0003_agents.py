"""Create agents and agent_permissions tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("goal_template", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "autonomy_mode",
            sa.String(50),
            nullable=False,
            server_default="bounded-autonomous",
        ),
        sa.Column("connector_ids", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("trigger_config", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
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
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
    )
    op.create_index("ix_agents_tenant_id", "agents", ["tenant_id"])
    op.create_index("ix_agents_tenant_status", "agents", ["tenant_id", "is_active"])

    # RLS policy
    op.execute("ALTER TABLE agents ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY agents_tenant_isolation ON agents "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    op.create_table(
        "agent_permissions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(32),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("tool_name", sa.String(200), nullable=False),
        sa.Column("level", sa.String(20), nullable=False, server_default="allow_log"),
        sa.Column("daily_limit", sa.Integer, nullable=True),
        sa.Column("per_goal_limit", sa.Integer, nullable=True),
        sa.Column("scope_pattern", sa.String(500), nullable=True),
        sa.UniqueConstraint("agent_id", "tool_name", name="uq_agent_tool"),
    )
    op.create_index("ix_agent_permissions_agent_id", "agent_permissions", ["agent_id"])
    op.create_index("ix_agent_permissions_tenant_id", "agent_permissions", ["tenant_id"])

    op.execute("ALTER TABLE agent_permissions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_permissions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY agent_permissions_tenant_isolation ON agent_permissions "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS agent_permissions_tenant_isolation ON agent_permissions"
    )
    op.drop_table("agent_permissions")
    op.execute("DROP POLICY IF EXISTS agents_tenant_isolation ON agents")
    op.drop_table("agents")
