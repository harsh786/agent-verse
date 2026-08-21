"""add_memory_conflicts_table

Revision ID: 0088_memory_conflicts
Revises: 0087_orchestration_tables
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_conflicts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("memory_id_a", sa.String(32), nullable=False),
        sa.Column("memory_id_b", sa.String(32), nullable=True),
        sa.Column(
            "conflict_type",
            sa.String(50),
            nullable=False,
            server_default="contradiction",
        ),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column(
            "resolved",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("resolution", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("memory_conflicts")
