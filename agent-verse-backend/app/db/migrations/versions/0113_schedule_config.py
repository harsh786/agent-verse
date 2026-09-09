"""Add generic JSONB `config` column to schedules.

Holds family-specific trigger config (file_drop_path, file_pattern, rss_url,
poll_url, ...) that has no dedicated column, so beat pollers can actually fire
data/file/polling triggers (previously FILE_DROP never fired because the watch
path was never persisted).

Revision ID: 0113
Revises: 0112
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0113"
down_revision = "0112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schedules",
        sa.Column(
            "config",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("schedules", "config")
