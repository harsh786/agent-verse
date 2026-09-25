"""Indexes for the Graph RAG lookup predicates on knowledge_nodes.

`app/rag/agentic/patterns/graph.py` selects seed/entity nodes with

    WHERE node.tenant_id = :tenant_id
      AND node.node_type IN ('entity','concept')
      AND ( node.source_id = ANY(:seed_chunk_ids)
            OR to_tsvector('english', node.label || ' ' || COALESCE(node.content,''))
               @@ plainto_tsquery('english', :query) )

Only `tenant_id` was indexed (ix_kn_tenant_type / ix_kn_tenant_label), so at
volume EXPLAIN shows a Bitmap Index Scan on the tenant followed by a Filter —
i.e. every node belonging to the tenant is fetched from the heap and then
discarded, and `to_tsvector` is recomputed per row. Measured on 40k nodes / 50
tenants: the seed lookup read 717 heap blocks and removed 798 rows by filter to
return 2. That cost is O(nodes-per-tenant) for every Graph RAG query, which is
the wrong shape for a tenant holding millions of documents.

Adds:
  * ix_kn_tenant_source  — btree (tenant_id, source_id) for the seed-chunk lookup.
  * ix_kn_content_fts    — GIN on the exact to_tsvector expression the query
                           uses, so it is an index condition rather than a
                           per-row recomputation. The planner can BitmapAnd it
                           with the tenant index.
  * ix_kn_metadata_gin   — GIN on CAST(extra_metadata AS jsonb), matching the
                           `CAST(... AS jsonb) @> CAST(:metadata_filter AS jsonb)`
                           containment filter (the column is JSON, not JSONB,
                           so the cast must be part of the index expression).

The index expressions must match the query text exactly or Postgres will not use
them — keep them in sync with graph.py.

Revision ID: e83b17c40d92
Revises: c7a41d9e52b0
"""

from __future__ import annotations

from alembic import op

revision = "e83b17c40d92"
down_revision = "c7a41d9e52b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kn_tenant_source "
        "ON knowledge_nodes (tenant_id, source_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kn_content_fts ON knowledge_nodes "
        "USING gin (to_tsvector('english', label || ' ' || COALESCE(content, '')))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kn_metadata_gin ON knowledge_nodes "
        "USING gin (CAST(extra_metadata AS jsonb) jsonb_path_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_kn_metadata_gin")
    op.execute("DROP INDEX IF EXISTS ix_kn_content_fts")
    op.execute("DROP INDEX IF EXISTS ix_kn_tenant_source")
