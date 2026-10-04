"""long_term_memory: (tenant_id, md5(content), created_at DESC) index (MEM-47)

The daily LTM dedup window-sorted every memory of a tenant by content in each
of up to 50 batches. It now finds the duplicate groups once per tenant
(GROUP BY md5(content) HAVING count(*) > 1) and deletes within those groups
only; both read through this expression index. An expression index needs no
new column, no table rewrite and no write-path change, and is built
CONCURRENTLY so a large table is not write-locked.

Revision ID: a47e3c9d1b52
Revises: e47c1d3f5a96
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a47e3c9d1b52"
down_revision: str | None = "e47c1d3f5a96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_long_term_memory_tenant_content_md5 "
            "ON long_term_memory (tenant_id, md5(content), created_at DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_long_term_memory_tenant_content_md5")
