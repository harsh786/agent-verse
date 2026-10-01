"""memory_records: GIN trigram index on safe_summary (MEM-09)

Lexical canonical-memory recall orders by ``similarity(safe_summary, query)``;
without a pg_trgm index that is a sequential scan of the tenant's records.

Revision ID: d94f6b8c0e53
Revises: c83e5a7b9d42
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d94f6b8c0e53"
down_revision: str | None = "c83e5a7b9d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_records_safe_summary_trgm "
        "ON memory_records USING gin (safe_summary gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memory_records_safe_summary_trgm")
