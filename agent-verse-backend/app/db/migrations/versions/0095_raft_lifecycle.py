"""Add tenant-scoped RAFT datasets, jobs, and confirmation grants.

Revision ID: 0095_raft_lifecycle
Revises: 0094_knowledge_graph_rls
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_knowledge_collections_tenant_id_id",
        "knowledge_collections",
        ["tenant_id", "id"],
    )
    op.create_table(
        "raft_datasets",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "collection_id",
            sa.String(32),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_raft_datasets_tenant_id_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            ["knowledge_collections.tenant_id", "knowledge_collections.id"],
            name="fk_raft_datasets_tenant_collection",
            ondelete="CASCADE",
        ),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.Column("examples", postgresql.JSONB(), nullable=False),
        sa.Column("validation_errors", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "length(content_fingerprint) = 64",
            name="ck_raft_datasets_content_fingerprint",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_raft_datasets_tenant", "raft_datasets", ["tenant_id"])
    op.create_index("idx_raft_datasets_collection", "raft_datasets", ["collection_id"])
    op.create_index(
        "idx_raft_datasets_tenant_collection",
        "raft_datasets",
        ["tenant_id", "collection_id"],
    )
    op.create_table(
        "raft_fine_tune_jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            sa.String(32),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["raft_datasets.tenant_id", "raft_datasets.id"],
            name="fk_raft_jobs_tenant_dataset",
            ondelete="CASCADE",
        ),
        sa.Column("collection_id", sa.String(32), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("base_model", sa.String(200), nullable=False),
        sa.Column("capability", sa.String(100), nullable=False),
        sa.Column("compatibility_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("confirmation_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_job_id", sa.String(200)),
        sa.Column("fine_tuned_model", sa.String(200)),
        sa.Column("evaluation", postgresql.JSONB()),
        sa.Column("cost_currency", sa.String(8)),
        sa.Column("estimated_cost", sa.Numeric(20, 6)),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'reconciling', 'submitted', 'running', 'completed', 'failed')",
            name="ck_raft_jobs_status",
        ),
        sa.CheckConstraint("version >= 0", name="ck_raft_jobs_version_nonnegative"),
        sa.CheckConstraint(
            "estimated_cost IS NULL OR (estimated_cost >= 0 AND "
            "estimated_cost::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
            name="ck_raft_jobs_cost_nonnegative",
        ),
        sa.CheckConstraint(
            "(cost_currency IS NULL) = (estimated_cost IS NULL)",
            name="ck_raft_jobs_cost_fields_paired",
        ),
        sa.CheckConstraint(
            "cost_currency IS NULL OR cost_currency ~ '^[A-Z]{3}$'",
            name="ck_raft_jobs_currency",
        ),
        sa.CheckConstraint(
            "length(compatibility_key) = 64 AND length(confirmation_digest) = 64",
            name="ck_raft_jobs_durable_hashes",
        ),
        sa.CheckConstraint(
            "status != 'completed' OR fine_tuned_model IS NOT NULL",
            name="ck_raft_jobs_completed_model",
        ),
        sa.CheckConstraint(
            "status NOT IN ('submitted', 'running', 'completed') OR provider_job_id IS NOT NULL",
            name="ck_raft_jobs_provider_identity",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_raft_jobs_tenant", "raft_fine_tune_jobs", ["tenant_id"])
    op.create_index("idx_raft_jobs_dataset", "raft_fine_tune_jobs", ["dataset_id"])
    op.create_index(
        "idx_raft_jobs_tenant_dataset",
        "raft_fine_tune_jobs",
        ["tenant_id", "dataset_id"],
    )
    op.create_index("idx_raft_jobs_compatibility", "raft_fine_tune_jobs", ["compatibility_key"])
    op.create_index(
        "idx_raft_jobs_compatible_model",
        "raft_fine_tune_jobs",
        ["tenant_id", "collection_id", "status", "updated_at"],
    )
    op.create_table(
        "raft_confirmation_grants",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            sa.String(32),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["raft_datasets.tenant_id", "raft_datasets.id"],
            name="fk_raft_grants_tenant_dataset",
            ondelete="CASCADE",
        ),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("base_model", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("estimated_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "estimated_amount >= 0 AND "
            "estimated_amount::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_raft_grants_amount_nonnegative",
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_raft_grants_currency"),
        sa.CheckConstraint(
            "length(token_hash) = 64 AND length(binding_digest) = 64",
            name="ck_raft_grants_durable_hashes",
        ),
    )
    op.create_index("idx_raft_confirmations_tenant", "raft_confirmation_grants", ["tenant_id"])
    op.create_index(
        "idx_raft_grants_tenant_dataset",
        "raft_confirmation_grants",
        ["tenant_id", "dataset_id"],
    )
    op.execute("""
        CREATE FUNCTION enforce_raft_job_transition() RETURNS trigger AS $$
        BEGIN
            IF NEW.version != OLD.version + 1 THEN
                RAISE EXCEPTION 'RAFT job version must increase by one';
            END IF;
            IF NOT (
                NEW.status = OLD.status
                OR (
                    OLD.status = 'pending'
                    AND NEW.status IN ('reconciling', 'submitted', 'failed')
                )
                OR (
                    OLD.status = 'reconciling'
                    AND NEW.status IN ('submitted', 'failed')
                )
                OR (
                    OLD.status = 'submitted'
                    AND NEW.status IN ('running', 'completed', 'failed')
                )
                OR (
                    OLD.status = 'running'
                    AND NEW.status IN ('completed', 'failed')
                )
            ) THEN
                RAISE EXCEPTION 'Illegal RAFT job status transition: % -> %',
                    OLD.status, NEW.status;
            END IF;
            IF OLD.evaluation IS NOT NULL AND NEW.evaluation IS NULL THEN
                RAISE EXCEPTION 'RAFT job evaluation cannot be erased';
            END IF;
            IF OLD.fine_tuned_model IS NOT NULL
                AND NEW.fine_tuned_model IS DISTINCT FROM OLD.fine_tuned_model THEN
                RAISE EXCEPTION 'RAFT fine-tuned model cannot change';
            END IF;
            IF OLD.provider_job_id IS NOT NULL
                AND NEW.provider_job_id IS DISTINCT FROM OLD.provider_job_id THEN
                RAISE EXCEPTION 'RAFT provider job identity cannot change';
            END IF;
            IF NEW.cost_currency IS DISTINCT FROM OLD.cost_currency
                OR NEW.estimated_cost IS DISTINCT FROM OLD.estimated_cost THEN
                RAISE EXCEPTION 'RAFT confirmed cost cannot change';
            END IF;
            IF (
                NEW.tenant_id,
                NEW.dataset_id,
                NEW.collection_id,
                NEW.provider_id,
                NEW.base_model,
                NEW.capability,
                NEW.compatibility_key,
                NEW.confirmation_digest
            ) IS DISTINCT FROM (
                OLD.tenant_id,
                OLD.dataset_id,
                OLD.collection_id,
                OLD.provider_id,
                OLD.base_model,
                OLD.capability,
                OLD.compatibility_key,
                OLD.confirmation_digest
            ) THEN
                RAISE EXCEPTION 'RAFT job identity and compatibility are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER raft_job_transition_guard
        BEFORE UPDATE ON raft_fine_tune_jobs
        FOR EACH ROW EXECUTE FUNCTION enforce_raft_job_transition()
    """)
    for table in (
        "raft_datasets",
        "raft_fine_tune_jobs",
        "raft_confirmation_grants",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS raft_job_transition_guard ON raft_fine_tune_jobs")
    op.execute("DROP FUNCTION IF EXISTS enforce_raft_job_transition()")
    for table in (
        "raft_confirmation_grants",
        "raft_fine_tune_jobs",
        "raft_datasets",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
    op.drop_constraint(
        "uq_knowledge_collections_tenant_id_id",
        "knowledge_collections",
        type_="unique",
    )
