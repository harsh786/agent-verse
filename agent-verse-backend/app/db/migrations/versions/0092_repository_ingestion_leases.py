"""Add repository ingestion leases and job-to-chunk integrity.

Revision ID: 0092_repository_ingestion_leases
Revises: 0091_rag_ingestion_structures
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op

revision = "0092_repository_ingestion_leases"
down_revision = "0091_rag_ingestion_structures"
branch_labels = None
depends_on = None

_DIMENSIONS = (768, 1024, 1536, 3072)


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS job_source_hash TEXT")
    op.execute("ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS lease_owner TEXT")
    op.execute(
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ"
    )
    op.execute("""
        UPDATE knowledge_documents
        SET job_source_hash = md5(source_url)
        WHERE domain_metadata->>'record_type' = 'ingestion_job'
          AND job_source_hash IS NULL
    """)
    op.execute("""
        UPDATE knowledge_documents
        SET status = 'failed', error_message = 'Repository ingestion interrupted',
            lease_owner = NULL, lease_expires_at = NULL
        WHERE domain_metadata->>'record_type' = 'ingestion_job'
          AND status = 'running'
    """)
    op.execute(
        "ALTER TABLE knowledge_documents "
        "DROP CONSTRAINT IF EXISTS uq_knowledge_documents_job_scope"
    )
    op.execute(
        "ALTER TABLE knowledge_documents "
        "ADD CONSTRAINT uq_knowledge_documents_job_scope "
        "UNIQUE (id, tenant_id, collection_id)"
    )
    op.execute(
        "ALTER TABLE knowledge_documents "
        "DROP CONSTRAINT IF EXISTS ck_repository_ingestion_job_state"
    )
    op.execute("""
        ALTER TABLE knowledge_documents
        ADD CONSTRAINT ck_repository_ingestion_job_state
        CHECK (
            domain_metadata->>'record_type' IS DISTINCT FROM 'ingestion_job'
            OR (
                status IN ('queued', 'running', 'completed', 'failed')
                AND job_source_hash IS NOT NULL
                AND (
                    (status = 'running' AND lease_owner IS NOT NULL
                     AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL)
                    OR
                    (status <> 'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)
                )
            )
        )
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_repository_ingestion_job_transition()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.domain_metadata->>'record_type' = 'ingestion_job' THEN
                IF OLD.status IN ('completed', 'failed') AND NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'terminal repository ingestion job is immutable';
                END IF;
                IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                    (OLD.status = 'queued' AND NEW.status IN ('running', 'failed'))
                    OR (OLD.status = 'running' AND NEW.status IN ('completed', 'failed'))
                ) THEN
                    RAISE EXCEPTION 'invalid repository ingestion job transition';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(
        "DROP TRIGGER IF EXISTS trg_repository_ingestion_job_transition "
        "ON knowledge_documents"
    )
    op.execute("""
        CREATE TRIGGER trg_repository_ingestion_job_transition
        BEFORE UPDATE ON knowledge_documents
        FOR EACH ROW EXECUTE FUNCTION enforce_repository_ingestion_job_transition()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_repository_chunk_job_integrity()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.ingestion_job_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM knowledge_documents AS job
                WHERE job.id = NEW.ingestion_job_id
                  AND job.tenant_id = NEW.tenant_id
                  AND job.collection_id = NEW.collection_id
                  AND job.source_type = 'repository'
                  AND job.status = 'running'
                  AND job.domain_metadata->>'record_type' = 'ingestion_job'
            ) THEN
                RAISE EXCEPTION 'invalid repository ingestion job association';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    for dimension in _DIMENSIONS:
        table = f"knowledge_chunks_{dimension}"
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS ingestion_job_id TEXT")
        op.execute(
            f"ALTER TABLE {table} "
            f"DROP CONSTRAINT IF EXISTS fk_{table}_ingestion_job_scope"
        )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_ingestion_job_scope "
            "FOREIGN KEY (ingestion_job_id, tenant_id, collection_id) "
            "REFERENCES knowledge_documents(id, tenant_id, collection_id)"
        )
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_job_integrity ON {table}")
        op.execute(
            f"CREATE TRIGGER trg_{table}_job_integrity BEFORE INSERT OR UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION enforce_repository_chunk_job_integrity()"
        )

    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_knowledge_documents_job_lease "
            "ON knowledge_documents(tenant_id, status, lease_expires_at) "
            "WHERE domain_metadata->>'record_type' = 'ingestion_job'"
        )
        for dimension in _DIMENSIONS:
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                f"idx_knowledge_chunks_{dimension}_ingestion_job "
                f"ON knowledge_chunks_{dimension}(tenant_id, ingestion_job_id) "
                "WHERE ingestion_job_id IS NOT NULL"
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for dimension in _DIMENSIONS:
            op.execute(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                f"idx_knowledge_chunks_{dimension}_ingestion_job"
            )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_knowledge_documents_job_lease")

    for dimension in _DIMENSIONS:
        table = f"knowledge_chunks_{dimension}"
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_job_integrity ON {table}")
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS fk_{table}_ingestion_job_scope")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS ingestion_job_id")
    op.execute("DROP FUNCTION IF EXISTS enforce_repository_chunk_job_integrity()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_repository_ingestion_job_transition "
        "ON knowledge_documents"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_repository_ingestion_job_transition()")
    op.execute(
        "ALTER TABLE knowledge_documents "
        "DROP CONSTRAINT IF EXISTS ck_repository_ingestion_job_state"
    )
    op.execute(
        "ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS uq_knowledge_documents_job_scope"
    )
    op.execute("ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS heartbeat_at")
    op.execute("ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS lease_expires_at")
    op.execute("ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS lease_owner")
    op.execute("ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS job_source_hash")
