"""execution_memory: trigram + (tenant, success, recency) indexes (MEM-36)

Execution-memory recall now selects by relevance in SQL
(``goal_text % :q OR :q <% goal_text`` ordered by trigram similarity), and the
blank-hint path reads ``(tenant_id, success) ORDER BY created_at DESC``. Without
these indexes both are sequential scans of a tenant's whole history.

Built CONCURRENTLY (outside the migration transaction) so a large table is not
write-locked during the build.

Revision ID: e36a1c7b2d40
Revises: 7cd9f383c99a
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e36a1c7b2d40"
down_revision: str | None = "7cd9f383c99a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_execution_memory_goal_text_trgm "
            "ON execution_memory USING gin (goal_text gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_execution_memory_tenant_success_created "
            "ON execution_memory (tenant_id, success, created_at DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_execution_memory_tenant_success_created")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_execution_memory_goal_text_trgm")
