"""collection_embedding_dim_2048 — allow 2048-d collections (NVIDIA nemotron).

Migration 0121 added the ``knowledge_chunks_2048`` dimension table so 2048-d
chunk vectors can be stored, but the ``ck_knowledge_collections_embedding_dim``
CHECK constraint on ``knowledge_collections`` still only permitted
(768, 1024, 1536, 3072). That left the configured NVIDIA embedder
(``nvidia/nemotron-3-embed-1b`` @ 2048-d) unable to create a collection at all
(CheckViolationError), so workflow RAG had no collection to retrieve from.

This widens the allowed set to include 2048, matching
``app.rag.store.SUPPORTED_EMBEDDING_DIMENSIONS``.

Revision ID: 0123
Revises: 0122
"""

from __future__ import annotations

from alembic import op

revision = "0123"
down_revision = "0122"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_knowledge_collections_embedding_dim"


def upgrade() -> None:
    op.execute(f"ALTER TABLE knowledge_collections DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(
        "ALTER TABLE knowledge_collections "
        f"ADD CONSTRAINT {_CONSTRAINT} "
        "CHECK (embedding_dim IN (768, 1024, 1536, 2048, 3072))"
    )


def downgrade() -> None:
    # Best-effort revert. Only re-narrow when no 2048-d collection exists, since
    # the old constraint would reject them and fail the migration.
    op.execute(f"ALTER TABLE knowledge_collections DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(
        "ALTER TABLE knowledge_collections "
        f"ADD CONSTRAINT {_CONSTRAINT} "
        "CHECK (embedding_dim IN (768, 1024, 1536, 3072))"
    )
