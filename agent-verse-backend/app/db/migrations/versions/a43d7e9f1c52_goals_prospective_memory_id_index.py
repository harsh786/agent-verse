"""goals: index the prospective intention a goal was fired from (MEM-43)

Firing a due intention first looks up the goal an earlier attempt already
produced (``execution_context ->> 'prospective_memory_id'``) so a lost
``complete`` never re-submits it. A partial expression index keeps that lookup
off the tenant's whole goal history (only prospective-fired goals are indexed).

Revision ID: a43d7e9f1c52
Revises: f35b2d8c4e61
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a43d7e9f1c52"
down_revision: str | None = "f35b2d8c4e61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_goals_prospective_memory_id "
            "ON goals (tenant_id, (execution_context ->> 'prospective_memory_id')) "
            "WHERE (execution_context ->> 'prospective_memory_id') IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_goals_prospective_memory_id")
