"""Indexes for the DB-backed ingestion/knowledge read endpoints.

The endpoints that used to return hardcoded stubs (``/ingestion/documents``,
``/ingestion/dlq``, ``/ingestion/cost``) and the semantic-cache pgvector
write-through now query real tables. Each gets an index matching its predicate
so none of them scans at corpus scale:

* ``knowledge_chunks_<dim>`` — (tenant_id, collection_id, metadata->>'source_id',
  document_id): ``/ingestion/documents`` groups a Source's chunks by document in
  index order and stops at ``limit`` (keyset pagination on document_id).
* ``ingestion_dlq`` — (tenant_id, created_at) WHERE resolved_at IS NULL: the
  tenant's open DLQ, newest first.
* ``ingestion_jobs`` — (tenant_id, created_at): this month's token usage.
* ``semantic_cache_entries`` — (tenant_id, created_at): TTL-bounded lookup and
  expiry purge of the write-through cache.

Revision ID: a9b8c7d6e5f4
Revises: e8f9a0b1c2d3
"""

from __future__ import annotations

from alembic import op

revision = "a9b8c7d6e5f4"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None

# Kept in sync with app.rag.store.SUPPORTED_EMBEDDING_DIMENSIONS.
_DIMENSIONS = (768, 1024, 1536, 2048, 3072)


def upgrade() -> None:
    for dim in _DIMENSIONS:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_{dim}_source_doc "
            f"ON knowledge_chunks_{dim} "
            "(tenant_id, collection_id, (metadata->>'source_id'), document_id)"
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ingestion_dlq_tenant_open "
        "ON ingestion_dlq (tenant_id, created_at) WHERE resolved_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_tenant_created "
        "ON ingestion_jobs (tenant_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_semantic_cache_tenant_created "
        "ON semantic_cache_entries (tenant_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_semantic_cache_tenant_created")
    op.execute("DROP INDEX IF EXISTS idx_ingestion_jobs_tenant_created")
    op.execute("DROP INDEX IF EXISTS idx_ingestion_dlq_tenant_open")
    for dim in _DIMENSIONS:
        op.execute(f"DROP INDEX IF EXISTS idx_knowledge_chunks_{dim}_source_doc")
