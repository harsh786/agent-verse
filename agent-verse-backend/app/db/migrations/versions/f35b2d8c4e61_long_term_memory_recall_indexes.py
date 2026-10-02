"""long_term_memory: trigram on content + (tenant, created_at DESC) (MEM-35)

The semantic recall path already has its halfvec(2048) HNSW index
(d26899b0a1e8). The other two LTM read paths did not:

* keyword fallback recall — ``content ILIKE '%term%'`` per query term — was a
  sequential scan of the tenant's memories (a GIN trigram index serves ILIKE);
* the paged list ``WHERE tenant_id = :tid ORDER BY created_at DESC`` sorted the
  tenant's whole set.

Built CONCURRENTLY so a large table is not write-locked during the build.

Revision ID: f35b2d8c4e61
Revises: e36a1c7b2d40
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f35b2d8c4e61"
down_revision: str | None = "e36a1c7b2d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_long_term_memory_content_trgm "
            "ON long_term_memory USING gin (content gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_long_term_memory_tenant_created "
            "ON long_term_memory (tenant_id, created_at DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_long_term_memory_tenant_created")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_long_term_memory_content_trgm")
