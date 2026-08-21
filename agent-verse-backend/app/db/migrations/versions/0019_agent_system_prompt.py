"""Add system_prompt to agents table.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("system_prompt", sa.Text, nullable=True, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("agents", "system_prompt")
