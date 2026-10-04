"""department_memory_entries: embedding vector(2048) + model, halfvec HNSW (MEM-42)

Department memory recall was keyword-substring scoring only, so a paraphrased
SOP or decision was never found. Entries are now embedded on add; recall
blends cosine similarity (same embedding model only) with the keyword share.

* ``embedding vector(2048)`` / ``embedding_model`` — nullable, so adding them
  is a catalogue change (no table rewrite); entries without a vector are
  still found lexically.
* HNSW over ``embedding::halfvec(2048)`` (plain-vector HNSW caps at 2000
  dims), built CONCURRENTLY.

Revision ID: b42d6e1f8a37
Revises: a47e3c9d1b52
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b42d6e1f8a37"
down_revision: str | None = "a47e3c9d1b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE department_memory_entries "
        "ADD COLUMN IF NOT EXISTS embedding vector(2048), "
        "ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(128)"
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_department_memory_embedding_halfvec "
            "ON department_memory_entries USING hnsw ((embedding::halfvec(2048)) "
            "halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_department_memory_embedding_halfvec")
    op.execute(
        "ALTER TABLE department_memory_entries "
        "DROP COLUMN IF EXISTS embedding_model, DROP COLUMN IF EXISTS embedding"
    )
