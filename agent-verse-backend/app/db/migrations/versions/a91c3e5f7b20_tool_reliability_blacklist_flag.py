"""tool_reliability_memory: blacklist flag instead of synthetic failures (MEM-01)

The self-improvement BLACKLIST_TOOL_PATTERN action used to write a synthetic
failure (latency 5000 ms) per blacklisted tool, and no real tool call ever
recorded an outcome — so every existing row is synthetic. A blacklist is now its
own flag (``blacklisted_at`` / ``blacklist_reason``) and counts come only from
real executions.

Data fix: rows carrying the synthetic signature (no successes, exactly 5000 ms
per failure) become a blacklist flag with their counts zeroed.

Revision ID: a91c3e5f7b20
Revises: d4e9a1c7b3f2
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a91c3e5f7b20"
down_revision: str | None = "d4e9a1c7b3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tool_reliability_memory"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("blacklisted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(_TABLE, sa.Column("blacklist_reason", sa.Text(), nullable=True))
    # FORCE RLS would hide every row from the migration role (no tenant GUC);
    # lift it for the one-off data fix and restore it in the same transaction.
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        UPDATE {_TABLE}
           SET blacklisted_at = last_used_at,
               blacklist_reason = 'blacklisted_by_self_improvement',
               failure_count = 0,
               total_latency_ms = 0.0
         WHERE success_count = 0
           AND failure_count > 0
           AND total_latency_ms = failure_count * 5000.0
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_column(_TABLE, "blacklist_reason")
    op.drop_column(_TABLE, "blacklisted_at")
