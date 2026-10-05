"""Index for the training-export retention sweep (NF-17)

``expire_training_exports`` scans finished jobs by completion time across all
tenants (``status = 'complete' AND completed_at < cutoff ORDER BY completed_at
LIMIT n``); a partial index keeps that a bounded index range scan however many
jobs exist. Built CONCURRENTLY (no write lock).

Revision ID: c3f9a1d7e5b2
Revises: ec9ee37e6779
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c3f9a1d7e5b2"
down_revision: str | None = "ec9ee37e6779"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_training_export_jobs_complete_completed"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX} "
            "ON training_export_jobs (completed_at) WHERE status = 'complete'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
