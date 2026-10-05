"""Partial index on active AI-Ops dataset runs (P7-1)

AI-Ops dataset runs are advanced by short, non-blocking worker steps; a beat
sweeper (``resume_stalled_ai_ops_runs``) re-dispatches runs whose step chain
died. Its cross-tenant scan reads only ``queued``/``running`` runs: this partial
index keeps that a bounded index range scan however many finished results the
table holds. Built CONCURRENTLY (no write lock).

Revision ID: e5b8c3d1a7f4
Revises: d4e7a2c9b1f3
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5b8c3d1a7f4"
down_revision: str | None = "d4e7a2c9b1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_ai_ops_eval_results_active"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX} "
            "ON ai_ops_eval_results (created_at) "
            "WHERE payload->>'status' IN ('queued', 'running')"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
