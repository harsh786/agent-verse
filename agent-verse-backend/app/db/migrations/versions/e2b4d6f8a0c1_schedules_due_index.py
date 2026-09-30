"""Index the beat's due-schedule scan on schedules.next_fire_at (TRG-15).

The beat now loads only DUE schedules from Postgres — ``WHERE tenant_id = :t
AND NOT paused AND (next_fire_at IS NULL OR next_fire_at <= now()) ORDER BY
next_fire_at`` — instead of every schedule (and every Redis key) each minute.
``next_fire_at`` has existed since 0007 but was never written or indexed; the
beat now maintains it. Existing rows keep NULL, which means "evaluate on the
next tick", after which the beat stores the real next time.

Revision ID: e2b4d6f8a0c1
Revises: c8d2f4a6b1e3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e2b4d6f8a0c1"
down_revision = "c8d2f4a6b1e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_schedules_due",
        "schedules",
        ["tenant_id", "next_fire_at"],
        postgresql_where=sa.text("NOT paused"),
    )


def downgrade() -> None:
    op.drop_index("ix_schedules_due", table_name="schedules")
