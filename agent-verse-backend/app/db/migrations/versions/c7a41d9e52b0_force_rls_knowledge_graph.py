"""FORCE row level security on the knowledge graph tables.

Migration 0094 ENABLEd RLS on ``knowledge_nodes`` / ``knowledge_edges`` and
attached a correct ``current_setting('app.tenant_id', TRUE)`` policy, but never
FORCEd it. Postgres exempts a table's *owner* from an ENABLE-only policy, so an
application that owns its own schema — the normal outcome when the app runs its
own migrations — has RLS silently inert on the knowledge graph.

Every other sensitive table in this codebase sets both (audit_events 0057,
policy_evaluations / policy_versions 0056, compliance_requests, marketplace_installs
0059, ...); these two were the exception.

This is safe to force: every code path that reads or writes these tables already
establishes the GUC — ``app/knowledge_graph/store.py`` and
``app/knowledge_graph/ingestion_hook.py`` (writes),
``app/lifecycle/deletion_orchestrator.py`` (erasure), and
``app/rag/agentic/patterns/graph.py``, whose docstring states it reads "in an
existing RLS scope" and whose caller ``app/rag/gateway.py`` wraps the session in
``sqlalchemy_rls_context``.

Revision ID: c7a41d9e52b0
Revises: 3f2bbce84e68
"""

from __future__ import annotations

from alembic import op

revision = "c7a41d9e52b0"
down_revision = "3f2bbce84e68"
branch_labels = None
depends_on = None

_TABLES = ("knowledge_nodes", "knowledge_edges")


def upgrade() -> None:
    for table in _TABLES:
        # Idempotent: ENABLE is already set by 0094, re-asserting is harmless.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
