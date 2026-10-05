"""source_configs.sync_fence — fencing token for the per-Source sync lock (TG-12).

The sync lock is a Redis key with a TTL that the holder renews. A holder that
stalls past the TTL can lose it while still running; ``sync_fence`` makes that
harmless. Each run that takes the lock bumps it and keeps the new value; its
cursor commits carry ``AND sync_fence = :fence`` and so match no row once a
newer run took over. A constant-default column: metadata-only on PG 11+, no
table rewrite.

Revision ID: d9f3b6a1c8e4
Revises: 700ac039283e
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d9f3b6a1c8e4"
down_revision: str | None = "700ac039283e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE source_configs ADD COLUMN IF NOT EXISTS sync_fence BIGINT NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE source_configs DROP COLUMN IF EXISTS sync_fence")
