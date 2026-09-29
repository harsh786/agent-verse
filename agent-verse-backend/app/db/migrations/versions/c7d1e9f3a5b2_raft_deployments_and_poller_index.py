"""RAFT model deployments and the in-flight status-poller index.

* ``raft_model_deployments`` — at most one deployed (serving) RAFT job per tenant
  collection. The composite FK to ``raft_fine_tune_jobs (tenant_id,
  collection_id, id)`` makes it impossible to deploy another tenant's job or a
  job trained on a different collection. Tenant-isolated by FORCE'd RLS.
* ``idx_raft_jobs_in_flight`` — partial index backing the Celery beat poller's
  bounded, oldest-first scan of submitted/running jobs.

Revision ID: c7d1e9f3a5b2
Revises: b4e6c8a0d2f1
"""

from __future__ import annotations

from alembic import op

revision = "c7d1e9f3a5b2"
down_revision = "b4e6c8a0d2f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE raft_fine_tune_jobs ADD CONSTRAINT uq_raft_jobs_tenant_collection_id "
        "UNIQUE (tenant_id, collection_id, id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_raft_jobs_in_flight "
        "ON raft_fine_tune_jobs (updated_at, id) "
        "WHERE status IN ('submitted', 'running')"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS raft_model_deployments (
            tenant_id VARCHAR(32) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            collection_id VARCHAR(32) NOT NULL,
            job_id VARCHAR(32) NOT NULL,
            deployed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, collection_id),
            CONSTRAINT fk_raft_deployments_tenant_collection
                FOREIGN KEY (tenant_id, collection_id)
                REFERENCES knowledge_collections (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_raft_deployments_tenant_collection_job
                FOREIGN KEY (tenant_id, collection_id, job_id)
                REFERENCES raft_fine_tune_jobs (tenant_id, collection_id, id)
                ON DELETE CASCADE
        )
        """
    )
    op.execute("ALTER TABLE raft_model_deployments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE raft_model_deployments FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS raft_model_deployments_tenant_isolation ON raft_model_deployments"
    )
    op.execute(
        "CREATE POLICY raft_model_deployments_tenant_isolation ON raft_model_deployments "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS raft_model_deployments_tenant_isolation ON raft_model_deployments"
    )
    op.execute("DROP TABLE IF EXISTS raft_model_deployments")
    op.execute("DROP INDEX IF EXISTS idx_raft_jobs_in_flight")
    op.execute(
        "ALTER TABLE raft_fine_tune_jobs DROP CONSTRAINT IF EXISTS "
        "uq_raft_jobs_tenant_collection_id"
    )
