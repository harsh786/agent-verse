"""long_term_memory halfvec hnsw ann index

X12-ANN (distributed-scale audit). long_term_memory.embedding is vector(2048),
which exceeds pgvector's 2000-d cap for a plain vector HNSW index, so cross-session
memory recall did an exact full scan per query. Add a halfvec(2048) HNSW cosine
index; the recall query casts embedding to halfvec(2048) to use it.

Revision ID: d26899b0a1e8
Revises: 5ed9686ad37b
Create Date: 2026-09-16 13:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d26899b0a1e8"
down_revision: str | None = "5ed9686ad37b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guard on the halfvec type existing (pgvector >= 0.7) so the migration is a
    # no-op on older pgvector rather than failing the whole upgrade chain.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'halfvec') THEN
                CREATE INDEX IF NOT EXISTS idx_long_term_memory_embedding_halfvec
                    ON long_term_memory
                    USING hnsw ((embedding::halfvec(2048)) halfvec_cosine_ops);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_long_term_memory_embedding_halfvec")
