"""D1: indexes for the same-source, same-name re-upload lookup.

A re-upload replaces the earlier upload of the same file name in the same
collection, and only of the same source (D1). The lookup
(``KnowledgeStore.same_name_document_ids_async``) matches
``metadata->>'source_file'`` OR ``metadata->>'doc_title'`` inside one
(tenant, collection); these two expression indexes let Postgres answer it with a
BitmapOr instead of scanning every chunk of a large collection per upload.
Built CONCURRENTLY so a live chunk table keeps taking writes.

Revision ID: e1a2b3c4d5f6
Revises: d9f3b6a1c8e4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1a2b3c4d5f6"
down_revision = "d9f3b6a1c8e4"
branch_labels = None
depends_on = None

# Kept in sync with app.rag.store.SUPPORTED_EMBEDDING_DIMENSIONS.
_DIMENSIONS = (768, 1024, 1536, 2048, 3072)
_KEYS = ("source_file", "doc_title")


def _create_index_concurrently(index_name: str, statement: str) -> None:
    """Retry an interrupted concurrent build without replacing a valid index."""
    is_valid = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT index.indisvalid FROM pg_index AS index "
                "JOIN pg_class AS relation ON relation.oid = index.indexrelid "
                "WHERE relation.relname = :index_name"
            ),
            {"index_name": index_name},
        )
        .scalar_one_or_none()
    )
    if is_valid is False:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")
    op.execute(statement)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for dim in _DIMENSIONS:
            table = f"knowledge_chunks_{dim}"
            for key in _KEYS:
                name = f"idx_{table}_{key}"
                _create_index_concurrently(
                    name,
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                    f"ON {table} (tenant_id, collection_id, (metadata->>'{key}'))",
                )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for dim in _DIMENSIONS:
            for key in _KEYS:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS idx_knowledge_chunks_{dim}_{key}")
