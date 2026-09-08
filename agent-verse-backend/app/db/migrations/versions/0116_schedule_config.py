"""Add a generic ``config`` JSONB column to ``schedules``.

The Celery beat loop (``fire_due_schedules``) reads family-specific config from
the discovered schedule dict (e.g. ``file_watch_path`` for FILE_DROP, ``rss_url``
for RSS_FEED, ``poll_url`` for API_POLL), but the schedules table had no columns
for them — so those fields never reached the worker and the triggers could not
fire. This adds one JSONB column to carry them (and future family fields)
without a column per family. (2.W-1)

Revision ID: 0116
Revises: 0115
"""

from __future__ import annotations

from alembic import op

revision = "0116"
down_revision = "0115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS config")
