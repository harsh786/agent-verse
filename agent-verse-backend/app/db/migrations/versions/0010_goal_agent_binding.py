"""Add goal agent binding workflow metadata.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "goals",
        sa.Column(
            "workflow_mode",
            sa.String(40),
            nullable=False,
            server_default="single_agent",
        ),
    )
    op.add_column(
        "goals",
        sa.Column(
            "execution_context",
            sa.JSON,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("goals", "execution_context")
    op.drop_column("goals", "workflow_mode")
