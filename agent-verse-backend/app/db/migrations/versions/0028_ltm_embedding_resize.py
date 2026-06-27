"""Resize long_term_memory embedding column to 1536 dimensions.

This covers all supported embedding providers:
  - OpenAI text-embedding-3-small: 1536 dims
  - OpenAI text-embedding-ada-002: 1536 dims
  - Voyage voyage-3: 1024 dims (fits in 1536)
  - sentence-transformers all-MiniLM-L6-v2 (local): smaller dims (fits in 1536)
"""

from alembic import op
import os

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


def upgrade() -> None:
    # Drop old index first (required before changing column type)
    op.execute("DROP INDEX IF EXISTS ix_ltm_embedding_hnsw")
    # Drop old column and recreate at new dimension
    op.execute("ALTER TABLE long_term_memory DROP COLUMN IF EXISTS embedding")
    op.execute(
        f"ALTER TABLE long_term_memory ADD COLUMN IF NOT EXISTS embedding vector({_DIM})"
    )
    # Recreate HNSW index at new dimension
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_ltm_embedding_hnsw
        ON long_term_memory
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ltm_embedding_hnsw")
    op.execute("ALTER TABLE long_term_memory DROP COLUMN IF EXISTS embedding")
    # Restore 768-dim
    op.execute("ALTER TABLE long_term_memory ADD COLUMN IF NOT EXISTS embedding vector(768)")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ltm_embedding_hnsw
        ON long_term_memory
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )
