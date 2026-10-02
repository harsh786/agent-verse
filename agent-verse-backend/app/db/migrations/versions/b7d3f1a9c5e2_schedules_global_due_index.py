"""Global due-schedule index for the single cross-tenant beat claim (TRG-15).

The beat used to run one ``WHERE tenant_id = :t ... ORDER BY next_fire_at``
query per active tenant (served by ``ix_schedules_due (tenant_id,
next_fire_at)``). It now claims the due rows of every tenant in one statement
ordered by ``next_fire_at`` alone, which needs an index led by
``next_fire_at``. Built CONCURRENTLY so a large ``schedules`` table is not
write-locked during the deploy.

Revision ID: b7d3f1a9c5e2
Revises: a6c1e9d4f2b7
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b7d3f1a9c5e2"
down_revision: str | None = "a6c1e9d4f2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_schedules_due_global "
            "ON schedules (next_fire_at ASC NULLS FIRST) WHERE NOT paused"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_schedules_due_global")
