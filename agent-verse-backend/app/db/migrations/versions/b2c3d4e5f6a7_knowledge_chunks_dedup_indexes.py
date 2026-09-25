"""Index the per-document dedup probe on every knowledge_chunks_<dim> table.

``KnowledgeStore.exists_by_hash`` runs once for **every** document the ingestion
pipeline touches (pipeline stage 3, plus the RPA/OCR pre-checks), and
``_persist_chunks`` repeats the ``doc_content_hash`` half of it inside the
ingest transaction as its TOCTOU guard. Both filter on::

    tenant_id = :t AND collection_id = :c
    AND (content_hash = :h OR metadata->>'doc_content_hash' = :h)

Neither ``content_hash`` nor that JSONB expression had any index — migration
0062 created only the HNSW vector index, ``(tenant_id, collection_id)``, a GIN
trigram index on ``content``, and a partial index on ``expires_at``. So the
hottest query in a bulk load could only be answered by reading every chunk in
the collection: at a million documents (~10M chunks) each further document
scanned 10M rows before it could be admitted.

Two B-tree indexes per dimension, both led by ``(tenant_id, collection_id)`` so
they serve the scoped probe directly and let the planner BitmapOr the two arms
of the OR instead of falling back to a scan.

Non-unique deliberately: ``doc_content_hash`` duplicates may already exist in a
deployed collection (that is exactly what the racy pre-check let through), so a
UNIQUE index here could fail to build against real data. Dedup stays enforced in
``_persist_chunks`` under the collection row lock; this makes it cheap.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

from __future__ import annotations

from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

# Kept in sync with app.rag.store.SUPPORTED_EMBEDDING_DIMENSIONS.
_DIMENSIONS = (768, 1024, 1536, 2048, 3072)


def _table(dim: int) -> str:
    return f"knowledge_chunks_{dim}"


def upgrade() -> None:
    for dim in _DIMENSIONS:
        table = _table(dim)
        op.execute(
            f"CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_{dim}_content_hash "
            f"ON {table} (tenant_id, collection_id, content_hash)"
        )
        op.execute(
            f"CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_{dim}_doc_hash "
            f"ON {table} (tenant_id, collection_id, (metadata->>'doc_content_hash'))"
        )


def downgrade() -> None:
    for dim in _DIMENSIONS:
        op.execute(f"DROP INDEX IF EXISTS idx_knowledge_chunks_{dim}_doc_hash")
        op.execute(f"DROP INDEX IF EXISTS idx_knowledge_chunks_{dim}_content_hash")
