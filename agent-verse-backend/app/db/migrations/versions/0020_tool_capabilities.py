"""Create tool_capabilities table for OpenAPI-imported tool definitions.

Revision ID: 0020
Revises: 0019
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_capabilities",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("connector_id", sa.String(32), nullable=False),
        sa.Column("tool_name", sa.String(200), nullable=False),
        sa.Column(
            "description",
            sa.Text,
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "http_method",
            sa.String(10),
            nullable=False,
            server_default="GET",
        ),
        sa.Column("http_path", sa.String(500), nullable=False),
        sa.Column(
            "parameters_schema",
            sa.JSON,
            nullable=False,
            server_default="{}",
        ),
        sa.Column("response_schema", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_tool_cap_tenant", "tool_capabilities", ["tenant_id"])
    op.create_index(
        "ix_tool_cap_connector", "tool_capabilities", ["connector_id"]
    )


def downgrade() -> None:
    op.drop_table("tool_capabilities")
