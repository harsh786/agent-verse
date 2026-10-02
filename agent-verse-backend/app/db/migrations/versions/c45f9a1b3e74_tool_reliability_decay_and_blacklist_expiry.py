"""tool_reliability_memory: decayed counters + blacklist expiry (MEM-45)

* ``recent_success`` / ``recent_failure`` / ``decayed_at`` — exponentially
  decayed outcome counters (7-day half-life) that the reliability verdict uses,
  so a tool with old failures and recent successes becomes reliable again.
  Lifetime ``success_count`` / ``failure_count`` stay for display. Backfilled
  from the lifetime counts as of ``last_used_at``.
* ``blacklist_expires_at`` — a self-improvement blacklist lapses (it used to be
  permanent with no way to clear it). Existing flags expire 7 days after they
  were set.

Revision ID: c45f9a1b3e74
Revises: b44e8f0a2d63
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c45f9a1b3e74"
down_revision: str | None = "b44e8f0a2d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tool_reliability_memory"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("recent_success", sa.Float(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        _TABLE,
        sa.Column("recent_failure", sa.Float(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(_TABLE, sa.Column("decayed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        _TABLE, sa.Column("blacklist_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    # FORCE RLS hides every row from the migration role; lift it for the
    # one-off backfill and restore it in the same transaction.
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        UPDATE {_TABLE}
           SET recent_success = success_count,
               recent_failure = failure_count,
               decayed_at = last_used_at,
               blacklist_expires_at = CASE WHEN blacklisted_at IS NULL THEN NULL
                                           ELSE blacklisted_at + interval '7 days' END
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_column(_TABLE, "blacklist_expires_at")
    op.drop_column(_TABLE, "decayed_at")
    op.drop_column(_TABLE, "recent_failure")
    op.drop_column(_TABLE, "recent_success")
