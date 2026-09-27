"""Indexes for the SQL-backed knowledge graph store.

``KnowledgeGraphStore`` used to hydrate each tenant's *entire* graph into every
replica and answer all reads from dicts. It now queries Postgres directly, so
the access paths those dicts used to cover need indexes:

* ``(tenant_id, source_node_id)`` / ``(tenant_id, target_node_id)`` — BFS hops
  (``source_node_id = ANY(:frontier)``), incident-edge lookups, and the
  community-detection joins. Migration 0085 indexed the node columns alone,
  without the tenant, so every probe also filtered the rest of the fleet's edges.
* trigram GIN on ``label`` and ``content`` — ``query_nodes(search=...)`` keeps
  its case-insensitive *substring* semantics (``ILIKE '%q%'``), which a B-tree
  cannot serve and the existing English FTS index answers differently (whole
  words, stemmed).
* ``(tenant_id, confidence DESC, label)`` — the default listing order.
* ``(tenant_id, id)`` on both tables — keyset pagination for ``/export``, which
  used to dump the whole graph in one response.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""

from __future__ import annotations

from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None

_INDEXES = (
    ("ix_ke_tenant_source", "knowledge_edges (tenant_id, source_node_id)"),
    ("ix_ke_tenant_target", "knowledge_edges (tenant_id, target_node_id)"),
    ("ix_ke_tenant_id", "knowledge_edges (tenant_id, id)"),
    ("ix_kn_tenant_id_id", "knowledge_nodes (tenant_id, id)"),
    ("ix_kn_tenant_conf_label", "knowledge_nodes (tenant_id, confidence DESC, label)"),
    ("ix_kn_label_trgm", "knowledge_nodes USING gin (label gin_trgm_ops)"),
    ("ix_kn_content_trgm", "knowledge_nodes USING gin (content gin_trgm_ops)"),
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, target in _INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")


def downgrade() -> None:
    for name, _target in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
