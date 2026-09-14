"""trigger_dlq.failed_at / resolved_at → TIMESTAMP WITH TIME ZONE.

``trigger_dlq`` was created (migration 0106) with ``failed_at`` / ``resolved_at``
as naive ``TIMESTAMP`` columns, but the app writes tz-aware UTC datetimes
(``datetime.now(UTC)``) like the rest of the schema. Binding a tz-aware datetime
to a naive column makes asyncpg raise "can't subtract offset-naive and
offset-aware datetimes", so EVERY dead-letter write failed — a failed trigger
fire that should be captured for retry/inspection was silently lost instead.

Convert both columns to ``TIMESTAMPTZ`` (interpreting the existing naive values as
UTC) to match the writes and the rest of the schedule schema.

Revision ID: 0129
Revises: 0128
"""

from __future__ import annotations

from alembic import op

revision = "0129"
down_revision = "0128"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE trigger_dlq "
        "ALTER COLUMN failed_at TYPE TIMESTAMP WITH TIME ZONE "
        "USING failed_at AT TIME ZONE 'UTC'"
    )
    op.execute(
        "ALTER TABLE trigger_dlq "
        "ALTER COLUMN resolved_at TYPE TIMESTAMP WITH TIME ZONE "
        "USING resolved_at AT TIME ZONE 'UTC'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE trigger_dlq "
        "ALTER COLUMN failed_at TYPE TIMESTAMP "
        "USING failed_at AT TIME ZONE 'UTC'"
    )
    op.execute(
        "ALTER TABLE trigger_dlq "
        "ALTER COLUMN resolved_at TYPE TIMESTAMP "
        "USING resolved_at AT TIME ZONE 'UTC'"
    )
