"""Enforce tenant RLS on persisted knowledge graph tables.

Revision ID: 0094_knowledge_graph_rls
Revises: 0093_current_embedding_defaults
"""

from __future__ import annotations

from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("knowledge_nodes", "knowledge_edges"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
        )


def downgrade() -> None:
    for table in ("knowledge_edges", "knowledge_nodes"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
