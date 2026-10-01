"""knowledge_node_mentions: every chunk an entity was extracted from (KB-53).

A knowledge-graph node kept only its FIRST mention's ``source_id``. Deleting a
document then either removed an entity other documents still mention, or left
the deleted document's edges behind; GraphRAG seeded only from the first
mention; and random edge ids duplicated every edge on each re-extraction.

* ``knowledge_node_mentions (tenant_id, node_id, chunk_id, document_id)`` — one
  row per (node, chunk). PK (tenant_id, node_id, chunk_id) answers "does this
  node still have a mention"; (tenant_id, chunk_id) serves GraphRAG seeding and
  chunk deletes; (tenant_id, document_id) document deletes. RLS enabled and
  FORCEd with the same tenant policy as the graph tables.
* ``knowledge_edges (tenant_id, provenance)`` — edges are deleted by the chunk
  they were extracted from.
* Backfill: each existing node's ``source_id`` becomes its first mention.

Revision ID: e2c6d8a0b4f1
Revises: e1b5c7d9f3a2
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e2c6d8a0b4f1"
down_revision: str | None = "e1b5c7d9f3a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_node_mentions (
            tenant_id   VARCHAR(255) NOT NULL,
            node_id     VARCHAR(255) NOT NULL,
            chunk_id    VARCHAR(255) NOT NULL,
            document_id VARCHAR(255),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, node_id, chunk_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knm_tenant_chunk "
        "ON knowledge_node_mentions (tenant_id, chunk_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knm_tenant_document "
        "ON knowledge_node_mentions (tenant_id, document_id) WHERE document_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ke_tenant_provenance "
        "ON knowledge_edges (tenant_id, provenance)"
    )
    op.execute("ALTER TABLE knowledge_node_mentions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_node_mentions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY knowledge_node_mentions_tenant_isolation ON knowledge_node_mentions "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )
    op.execute(
        "INSERT INTO knowledge_node_mentions (tenant_id, node_id, chunk_id) "
        "SELECT tenant_id, id, source_id FROM knowledge_nodes "
        "WHERE source_id IS NOT NULL AND source_id <> '' "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ke_tenant_provenance")
    op.execute("DROP TABLE IF EXISTS knowledge_node_mentions")
