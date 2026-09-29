"""Canonical memory: persisted scope, sealed sensitive payloads, SQL-side recall.

``memory_records`` could not store what the canonical contract carries:

* ``agent_id`` / ``collection_id`` / ``source`` — the scoping dimensions of
  ``MemoryWriteRequest`` were silently dropped, so an agent-scoped recall on
  Postgres matched nothing (or, unscoped, everything).
* ``sealed_content`` — a confidential/restricted record got a
  ``memory://encrypted/<id>`` reference but no encrypted payload was stored
  anywhere: a dangling pointer. The vault-encrypted payload now lives here.
* ``embedding_source_model`` — which embedding model produced ``embedding``, so
  recall only compares vectors from the same model (narrower vectors are
  zero-padded into the 1536-d column, like long-term memory does).

Indexes back the SQL-side candidate queries of recall (tenant + agent scope +
recency, and cosine distance over the vector column).

``memory_backfill_checkpoints`` records the resumable position of the legacy →
canonical memory backfill (per tenant and source table). Tenant-isolated by
FORCE'd RLS like every other tenant table.

Revision ID: c8d2f4a6b1e3
Revises: b4e6c8a0d2f1
"""

from __future__ import annotations

from alembic import op

revision = "c8d2f4a6b1e3"
down_revision = "b4e6c8a0d2f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS agent_id VARCHAR(32)")
    op.execute("ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS collection_id VARCHAR(64)")
    op.execute("ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS source VARCHAR(64)")
    op.execute("ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS sealed_content TEXT")
    op.execute(
        "ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS embedding_source_model VARCHAR(128)"
    )
    # A sensitive record must carry its sealed payload — never a dangling
    # ``memory://encrypted/...`` reference.
    op.execute(
        "ALTER TABLE memory_records DROP CONSTRAINT IF EXISTS ck_memory_sensitive_sealed"
    )
    op.execute(
        "ALTER TABLE memory_records ADD CONSTRAINT ck_memory_sensitive_sealed CHECK ("
        "classification NOT IN ('confidential', 'restricted') OR sealed_content IS NOT NULL"
        ") NOT VALID"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_records_recall_scope ON memory_records "
        "(tenant_id, agent_id, memory_kind, lifecycle_state, updated_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_records_embedding_hnsw ON memory_records "
        "USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_backfill_checkpoints (
            tenant_id VARCHAR(32) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            source_table VARCHAR(64) NOT NULL,
            last_created_at TIMESTAMPTZ,
            last_source_id VARCHAR(64),
            rows_processed INTEGER NOT NULL DEFAULT 0,
            completed BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, source_table)
        )
        """
    )
    for table in ("memory_records", "memory_backfill_checkpoints"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS memory_backfill_checkpoints_tenant_isolation "
        "ON memory_backfill_checkpoints"
    )
    op.execute(
        "CREATE POLICY memory_backfill_checkpoints_tenant_isolation "
        "ON memory_backfill_checkpoints "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_backfill_checkpoints")
    op.execute("DROP INDEX IF EXISTS ix_memory_records_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_memory_records_recall_scope")
    op.execute(
        "ALTER TABLE memory_records DROP CONSTRAINT IF EXISTS ck_memory_sensitive_sealed"
    )
    for column in (
        "embedding_source_model",
        "sealed_content",
        "source",
        "collection_id",
        "agent_id",
    ):
        op.execute(f"ALTER TABLE memory_records DROP COLUMN IF EXISTS {column}")
