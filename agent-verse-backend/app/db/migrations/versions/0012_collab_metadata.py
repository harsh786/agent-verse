"""Add metadata to collaboration sessions.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "collab_sessions",
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index(
        "ux_collab_operations_session_version",
        "collab_operations",
        ["session_id", "version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_collab_operations_session_version", table_name="collab_operations")
    op.drop_column("collab_sessions", "metadata")
