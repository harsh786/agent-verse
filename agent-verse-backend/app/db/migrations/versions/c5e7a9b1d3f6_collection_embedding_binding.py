"""knowledge_collections.embedding_provider / embedding_model — per-collection embedders.

A knowledge collection is now bound to ONE embedding model (a Model Registry
entry or the deployment's default embedder) and its width; ``embedding_dim``
already selects the chunk table (``knowledge_chunks_768/1024/1536/2048/3072``).
Ingestion, re-embedding and every retrieval strategy embed for a collection
with ITS model (``app.rag.collection_embedders``), so collections of different
embedders and widths coexist instead of one global ``EMBEDDING_DIM`` refusing
every other model.

* ``embedding_provider`` — the bound model's provider. Set = an explicit binding
  (made at creation or by a re-embed), served by exactly that model.
* ``embedding_model`` — the bound model id. NULL = unbound: the default embedder.

Back-fill (existing collections keep their current embedder): ``embedding_model``
is derived from the ``embedder`` label — the model that wrote the collection's
vectors (USR-3) — unless the label names no model (``''`` / ``unknown`` / the
pre-USR-3 ``voyage``). ``embedding_provider`` stays NULL on every existing row:
a derived binding falls back to the default embedder when its model is not
configured any more and the widths agree, exactly as before. Additive only;
no vector is touched.

Revision ID: c5e7a9b1d3f6
Revises: a8c2e4f6b1d3
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e7a9b1d3f6"
down_revision: str | Sequence[str] | None = "a8c2e4f6b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_collections",
        sa.Column("embedding_provider", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "knowledge_collections",
        sa.Column("embedding_model", sa.Text(), nullable=True),
    )
    op.execute(
        "UPDATE knowledge_collections SET embedding_model = btrim(embedder) "
        "WHERE embedding_model IS NULL AND embedder IS NOT NULL "
        "AND lower(btrim(embedder)) NOT IN ('', 'unknown', 'voyage')"
    )


def downgrade() -> None:
    op.drop_column("knowledge_collections", "embedding_model")
    op.drop_column("knowledge_collections", "embedding_provider")
