"""Add embedding vector column to long_term_memory for pgvector semantic search."""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector if not already enabled
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Add embedding column (768-dim for voyage-3, text-embedding-3-small)
    op.execute(
        "ALTER TABLE long_term_memory ADD COLUMN IF NOT EXISTS embedding vector(768)"
    )
    # Create HNSW index for fast approximate nearest neighbor search
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ltm_embedding_hnsw
        ON long_term_memory
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ltm_embedding_hnsw")
    op.execute("ALTER TABLE long_term_memory DROP COLUMN IF EXISTS embedding")
