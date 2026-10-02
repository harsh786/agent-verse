"""prospective_memory: partial index for the terminal-row purge (MEM-44)

The daily retention purge deletes terminal intentions (completed / failed /
cancelled / expired) older than the retention window in batches. The existing
``ix_prospective_due`` is partial on pending/leased rows only, so without this
index every batch scanned the whole table.

Revision ID: b44e8f0a2d63
Revises: a43d7e9f1c52
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b44e8f0a2d63"
down_revision: str | None = "a43d7e9f1c52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_prospective_terminal_due "
            "ON prospective_memory (due_at) "
            "WHERE state IN ('completed', 'failed', 'cancelled', 'expired')"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_prospective_terminal_due")
