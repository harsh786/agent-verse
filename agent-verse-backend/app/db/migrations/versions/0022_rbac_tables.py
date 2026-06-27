"""Create user_roles and ip_allowlist tables.

Revision ID: 0019
Revises: 0018
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_roles",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.String(200), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "user_id", "role", name="uq_user_role"),
    )
    op.create_index("ix_user_roles_tenant", "user_roles", ["tenant_id"])
    op.create_index("ix_user_roles_user", "user_roles", ["user_id"])

    op.create_table(
        "ip_allowlist",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("cidr", sa.String(50), nullable=False),
        sa.Column(
            "description",
            sa.String(200),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_ip_allowlist_tenant", "ip_allowlist", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("ip_allowlist")
    op.drop_table("user_roles")
