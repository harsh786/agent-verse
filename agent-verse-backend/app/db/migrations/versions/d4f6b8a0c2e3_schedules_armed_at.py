"""schedules.armed_at: the instant a time trigger was (re)armed (B1-1)

A time trigger must never fire a slot from before it existed or was resumed.
The beat evaluated a never-fired cron from "the most recent slot <= now", so a
weekday-9am schedule created at 15:00 fired at once for that morning's 09:00,
and a resumed (or re-timed) schedule replayed every slot it was paused for
(up to 60 goals). ``armed_at`` is set on resume and on every spec edit; the
beat uses ``max(last_fired_at, armed_at or created_at)`` as the floor of the
slots it fires. Nullable and without a default: adding it is a catalog-only
change, and existing rows fall back to ``created_at``.

Revision ID: d4f6b8a0c2e3
Revises: a12c4e6f8b10
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6b8a0c2e3"
down_revision: str | None = "a12c4e6f8b10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "schedules", sa.Column("armed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("schedules", "armed_at")
