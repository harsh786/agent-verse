"""memory_records.embedding -> vector(2048) behind a halfvec HNSW index (MEM-38)

The column was vector(1536) and the default embedder (settings.embedding_dim)
is 2048-d, so no canonical record ever got a vector: every embedding was "too
wide" and semantic recall of Reflexion lessons never happened.

* The column becomes vector(2048). Existing 1536-d vectors are zero-padded
  (padding changes neither dot products nor norms, so cosine ranking is
  unchanged) and ``embedding_dimension`` records the new width.
* pgvector's plain-vector HNSW caps at 2000 dims, so the ANN index is rebuilt
  over ``embedding::halfvec(2048)`` (halfvec_cosine_ops), as for
  long_term_memory; recall orders by the same expression.

The 0104 CHECK (embedding_dimension = 1536) becomes = 2048. The type change
rewrites the table once (a one-off, like 0122 for LTM); the index is built
CONCURRENTLY afterwards.

Revision ID: e47c1d3f5a96
Revises: f1e790b4050a
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e47c1d3f5a96"
down_revision: str | None = "f1e790b4050a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIM = 2048


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memory_records_embedding_hnsw")
    # One ALTER TABLE, so the table is rewritten once: the vector widens, every
    # row's embedding_dimension becomes 2048 and the 0104 CHECK follows it.
    op.execute(
        "ALTER TABLE memory_records "
        "DROP CONSTRAINT IF EXISTS ck_memory_embedding_dimension, "
        f"ALTER COLUMN embedding TYPE vector({_DIM}) USING CASE WHEN embedding IS NULL "
        "THEN NULL ELSE CAST(array_cat(CAST(embedding AS real[]), "
        f"array_fill(0::real, ARRAY[{_DIM} - vector_dims(embedding)])) AS vector({_DIM})) END, "
        f"ALTER COLUMN embedding_dimension TYPE integer USING {_DIM}, "
        f"ALTER COLUMN embedding_dimension SET DEFAULT {_DIM}, "
        "ADD CONSTRAINT ck_memory_embedding_dimension "
        f"CHECK (embedding_dimension = {_DIM})"
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_memory_records_embedding_halfvec "
            f"ON memory_records USING hnsw ((embedding::halfvec({_DIM})) halfvec_cosine_ops) "
            "WITH (m = 16, ef_construction = 64)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_memory_records_embedding_halfvec")
    # Vectors wider than 1536 cannot be narrowed; they are dropped (the
    # maintenance sweep re-embeds them under the 1536-d profile).
    op.execute(
        "ALTER TABLE memory_records "
        "DROP CONSTRAINT IF EXISTS ck_memory_embedding_dimension, "
        "ALTER COLUMN embedding TYPE vector(1536) USING NULL, "
        "ALTER COLUMN embedding_source_model TYPE varchar(128) USING NULL, "
        "ALTER COLUMN embedding_dimension TYPE integer USING 1536, "
        "ALTER COLUMN embedding_dimension DROP DEFAULT, "
        "ADD CONSTRAINT ck_memory_embedding_dimension CHECK (embedding_dimension = 1536)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_records_embedding_hnsw ON memory_records "
        "USING hnsw (embedding vector_cosine_ops)"
    )
