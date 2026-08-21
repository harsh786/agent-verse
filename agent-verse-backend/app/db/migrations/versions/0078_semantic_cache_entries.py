"""Add semantic_cache_entries table with pgvector HNSW index.

Revision ID: 0078
Revises: 0077
Create Date: 2026-07-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "semantic_cache_entries",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("embedding", sa.Text, nullable=False),  # stored as text, cast to vector
        sa.Column("response", sa.Text, nullable=False),
        sa.Column("hit_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_semantic_cache_tenant", "semantic_cache_entries", ["tenant_id"])

    # RLS
    op.execute("ALTER TABLE semantic_cache_entries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE semantic_cache_entries FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON semantic_cache_entries
        USING (tenant_id = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
    """)

    # HNSW index — created after table; requires pgvector extension.
    # Dimension is not fixed at migration time; the index will be created over
    # the text column cast to vector(N) once the embedding dimension is known
    # (typically via a follow-up migration or manual DBA step).
    # Example for dimension 1536 (OpenAI):
    #   CREATE INDEX ON semantic_cache_entries
    #     USING hnsw ((embedding::vector(1536)) vector_cosine_ops)
    #     WITH (m=16, ef_construction=64);


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON semantic_cache_entries")
    op.drop_table("semantic_cache_entries")
