"""baseline: enable extensions + tenant RLS helper

Enables pgvector (vector similarity) and pg_trgm (full-text trigram matching) — the two
extensions the Agentic RAG hybrid search depends on — and installs the helper used by
every Row-Level-Security policy to read the current tenant from a session GUC.

Domain tables (tenants, agents, goals, documents, ...) and their per-table RLS policies
and HNSW/trigram indexes are added in the tenancy phase, each in its own migration.

Revision ID: 0001
Revises:
Create Date: 2026-06-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Every RLS policy compares a row's tenant_id against this function. It reads the
    # 'app.tenant_id' GUC set per-connection by the tenancy middleware; missing/empty
    # returns NULL so a policy denies access rather than leaking across tenants.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid AS $$
            SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid;
        $$ LANGUAGE sql STABLE;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS current_tenant_id()")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS vector")
