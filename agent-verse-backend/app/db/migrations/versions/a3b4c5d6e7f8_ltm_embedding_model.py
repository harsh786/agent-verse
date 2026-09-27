"""long_term_memory: record the embedding model behind each vector.

Narrower embeddings (e.g. 1024-d Qwen3-Embedding) are now zero-padded into the
fixed vector(2048) column instead of failing every write. Vectors from different
models are not comparable even at the same width, so each row records the model
(and native width) that produced it and recall only ranks rows of the query's
model. Legacy rows (NULL model) are treated as native 2048-d.

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
"""

from __future__ import annotations

from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE long_term_memory "
        "ADD COLUMN IF NOT EXISTS embedding_model TEXT, "
        "ADD COLUMN IF NOT EXISTS embedding_dim SMALLINT"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_long_term_memory_tenant_model "
        "ON long_term_memory (tenant_id, embedding_model)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_long_term_memory_tenant_model")
    op.execute(
        "ALTER TABLE long_term_memory DROP COLUMN IF EXISTS embedding_dim, "
        "DROP COLUMN IF EXISTS embedding_model"
    )
