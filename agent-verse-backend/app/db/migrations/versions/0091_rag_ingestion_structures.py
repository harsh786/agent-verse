"""Complete persisted RAG ingestion structures for every supported vector dimension.

Revision ID: 0091_rag_ingestion_structures
Revises: 0090_parent_child_retrieval
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None

_DIMENSIONS = (768, 1024, 1536, 3072)


def _create_index_concurrently(index_name: str, statement: str) -> None:
    """Retry an interrupted concurrent build without replacing a valid index."""
    is_valid = op.get_bind().execute(
        sa.text(
            "SELECT index.indisvalid FROM pg_index AS index "
            "JOIN pg_class AS relation ON relation.oid = index.indexrelid "
            "WHERE relation.relname = :index_name"
        ),
        {"index_name": index_name},
    ).scalar_one_or_none()
    if is_valid is False:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")
    op.execute(statement)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_collections "
        "DROP CONSTRAINT IF EXISTS ck_knowledge_collections_embedding_dim"
    )
    op.execute(
        "ALTER TABLE knowledge_collections "
        "ADD CONSTRAINT ck_knowledge_collections_embedding_dim "
        "CHECK (embedding_dim IN (768, 1024, 1536, 3072))"
    )
    op.execute(
        "DROP POLICY IF EXISTS knowledge_collections_isolation ON knowledge_collections"
    )
    op.execute(
        "CREATE POLICY knowledge_collections_isolation ON knowledge_collections "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )

    for dimension in _DIMENSIONS:
        table = f"knowledge_chunks_{dimension}"
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS window_id TEXT")
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS hierarchy_level "
            "INTEGER NOT NULL DEFAULT 0"
        )
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS is_proposition "
            "BOOLEAN NOT NULL DEFAULT FALSE"
        )
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS strategy_metadata "
            "JSONB NOT NULL DEFAULT '{}'::jsonb"
        )

        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
            "WITH CHECK ("
            "tenant_id = current_setting('app.tenant_id', TRUE) AND "
            "EXISTS (SELECT 1 FROM knowledge_collections AS collection "
            f"WHERE collection.id = {table}.collection_id "
            f"AND collection.tenant_id = {table}.tenant_id "
            "AND collection.is_active IS TRUE)"
            ")"
        )

    table = "knowledge_chunks_3072"
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS parent_chunk_id TEXT")
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
        "chunk_level VARCHAR(10) DEFAULT 'leaf'"
    )
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS window_start INTEGER")
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS window_end INTEGER")
    with op.get_context().autocommit_block():
        for dimension in _DIMENSIONS:
            table = f"knowledge_chunks_{dimension}"
            _create_index_concurrently(
                f"idx_{table}_window_id",
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{table}_window_id "
                f"ON {table}(window_id) WHERE window_id IS NOT NULL",
            )
            _create_index_concurrently(
                f"idx_{table}_hierarchy",
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{table}_hierarchy "
                f"ON {table}(collection_id, hierarchy_level)",
            )
            _create_index_concurrently(
                f"idx_{table}_proposition",
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{table}_proposition "
                f"ON {table}(collection_id) WHERE is_proposition IS TRUE",
            )
            _create_index_concurrently(
                f"idx_{table}_metadata",
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{table}_metadata "
                f"ON {table} USING gin (metadata jsonb_path_ops)",
            )
            _create_index_concurrently(
                f"idx_{table}_strategy_metadata",
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{table}_strategy_metadata "
                f"ON {table} USING gin (strategy_metadata jsonb_path_ops)",
            )
            _create_index_concurrently(
                f"idx_{table}_fts",
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{table}_fts "
                f"ON {table} USING gin (to_tsvector('english', content))",
            )
        _create_index_concurrently(
            "idx_knowledge_chunks_3072_vector_halfvec",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "idx_knowledge_chunks_3072_vector_halfvec ON knowledge_chunks_3072 "
            "USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops) "
            "WITH (m = 16, ef_construction = 64)",
        )
        _create_index_concurrently(
            "ix_knowledge_chunks_3072_parent_chunk_id",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_knowledge_chunks_3072_parent_chunk_id "
            "ON knowledge_chunks_3072(parent_chunk_id)",
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for dimension in _DIMENSIONS:
            table = f"knowledge_chunks_{dimension}"
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS idx_{table}_fts")
            op.execute(
                f"DROP INDEX CONCURRENTLY IF EXISTS idx_{table}_strategy_metadata"
            )
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS idx_{table}_metadata")
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS idx_{table}_proposition")
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS idx_{table}_hierarchy")
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS idx_{table}_window_id")
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "idx_knowledge_chunks_3072_vector_halfvec"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_knowledge_chunks_3072_parent_chunk_id"
        )

    for dimension in _DIMENSIONS:
        table = f"knowledge_chunks_{dimension}"
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS strategy_metadata")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS is_proposition")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS hierarchy_level")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS window_id")
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
        )

    table = "knowledge_chunks_3072"
    op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS window_end")
    op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS window_start")
    op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS chunk_level")
    op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS parent_chunk_id")

    op.execute(
        "ALTER TABLE knowledge_collections "
        "DROP CONSTRAINT IF EXISTS ck_knowledge_collections_embedding_dim"
    )
    op.execute(
        "DROP POLICY IF EXISTS knowledge_collections_isolation ON knowledge_collections"
    )
    op.execute(
        "CREATE POLICY knowledge_collections_isolation ON knowledge_collections "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )
