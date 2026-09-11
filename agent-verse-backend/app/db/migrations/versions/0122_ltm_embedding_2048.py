"""Resize long_term_memory.embedding to 2048 dims (NVIDIA nemotron / qwen3).

The active embedder (``settings.embedding_dim = 2048`` — NVIDIA
``nvidia/nemotron-3-embed-1b``) returns 2048-dimensional vectors, but the
``long_term_memory.embedding`` column was last sized to 1536 by migration 0028.
Every LTM write therefore failed with::

    asyncpg.exceptions.DataError: expected 1536 dimensions, not 2048

so no long-term memory was ever persisted. Resize the column to 2048.

Vector ANN index note: pgvector's HNSW/IVFFlat cap out at 2000 dimensions, so
(exactly as the 2048-d and 3072-d ``knowledge_chunks_*`` tables do in 0121) the
2048-d column gets **no** ``hnsw`` index — the old ``ix_ltm_embedding_hnsw`` is
dropped and not recreated. LTM cosine recall falls back to an exact scan, which
is fine at LTM's row counts.

Revision ID: 0122
Revises: 0121
"""

from __future__ import annotations

from alembic import op

revision = "0122"
down_revision = "0121"
branch_labels = None
depends_on = None

_DIM = 2048  # matches settings.embedding_dim (NVIDIA nemotron-3-embed-1b / qwen3)


def upgrade() -> None:
    # The HNSW index cannot exist on a >2000-dim column — drop it before (and
    # do not recreate it after) the resize.
    op.execute("DROP INDEX IF EXISTS ix_ltm_embedding_hnsw")
    # Drop + re-add the column: a dimension change can't preserve existing
    # vectors, and any 1536-d rows are unreadable by the 2048-d embedder anyway.
    op.execute("ALTER TABLE long_term_memory DROP COLUMN IF EXISTS embedding")
    op.execute(f"ALTER TABLE long_term_memory ADD COLUMN IF NOT EXISTS embedding vector({_DIM})")


def downgrade() -> None:
    # Restore the 0028 end-state: a 1536-d column with its HNSW index.
    op.execute("ALTER TABLE long_term_memory DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE long_term_memory ADD COLUMN IF NOT EXISTS embedding vector(1536)")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ltm_embedding_hnsw
        ON long_term_memory
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )
