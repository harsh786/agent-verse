"""0109 — source_configs and ingestion_jobs tables.

Revision ID: 0109
Revises: 0108_workflow_engine_tables
Create Date: 2026-08-17

These tables power the generic ingestion framework:
  source_configs  — configuration for every connected source
  ingestion_jobs  — job tracking with cursor, progress, status
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0109"
down_revision = "0108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s';")
    # ── source_configs ────────────────────────────────────────────────────────
    op.create_table(
        "source_configs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("family", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("sync_mode", sa.String(16), nullable=False, server_default="incremental"),
        sa.Column("sync_interval_seconds", sa.Integer, nullable=False, server_default="3600"),
        sa.Column("connection_config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("cursor_value", sa.Text, nullable=False, server_default=""),
        sa.Column("include_patterns", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("exclude_patterns", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("max_doc_size_bytes", sa.Integer, nullable=False, server_default="10485760"),
        sa.Column("chunking_strategy", sa.String(32), nullable=False, server_default="auto"),
        sa.Column("chunk_size_tokens", sa.Integer, nullable=False, server_default="512"),
        sa.Column("chunk_overlap_tokens", sa.Integer, nullable=False, server_default="64"),
        sa.Column("embedding_model", sa.String(64), nullable=False, server_default="auto"),
        sa.Column("language_hint", sa.String(16), nullable=False, server_default=""),
        sa.Column("inherit_source_acl", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("allowed_roles", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("min_quality_score", sa.Float, nullable=False, server_default="0.3"),
        sa.Column("pii_action", sa.String(16), nullable=False, server_default="redact"),
        sa.Column("freshness_ttl_seconds", sa.Integer, nullable=False, server_default="86400"),
        sa.Column("collection_id", sa.String(64), nullable=True),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_docs_indexed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_chunks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_source_configs_tenant", "source_configs", ["tenant_id"])
    op.create_index("idx_source_configs_type", "source_configs", ["tenant_id", "source_type"])
    op.create_index("idx_source_configs_family", "source_configs", ["tenant_id", "family"])
    op.create_index("idx_source_configs_enabled", "source_configs", ["tenant_id", "enabled"])

    op.execute("ALTER TABLE source_configs ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY source_configs_tenant_isolation ON source_configs
        USING (tenant_id = current_setting('app.tenant_id', true));
    """)

    # ── ingestion_jobs ────────────────────────────────────────────────────────
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("sync_mode", sa.String(16), nullable=False),
        sa.Column("triggered_by", sa.String(64), nullable=False, server_default="scheduler"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("docs_discovered", sa.Integer, nullable=False, server_default="0"),
        sa.Column("docs_indexed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("docs_skipped", sa.Integer, nullable=False, server_default="0"),
        sa.Column("docs_failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("chunks_created", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bytes_processed", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("tokens_consumed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cursor_before", sa.Text, nullable=False, server_default=""),
        sa.Column("cursor_after", sa.Text, nullable=False, server_default=""),
        sa.Column("error_message", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["source_configs.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_ingestion_jobs_source", "ingestion_jobs", ["source_id", "created_at"])
    op.create_index("idx_ingestion_jobs_tenant", "ingestion_jobs", ["tenant_id", "status"])

    op.execute("ALTER TABLE ingestion_jobs ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY ingestion_jobs_tenant_isolation ON ingestion_jobs
        USING (tenant_id = current_setting('app.tenant_id', true));
    """)

    # ── ingestion_dlq ─────────────────────────────────────────────────────────
    # (Extends 0106 trigger DLQ — reuses same table concept for ingestion)
    op.create_table(
        "ingestion_dlq",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("job_id", sa.String(64), nullable=True),
        sa.Column("doc_id", sa.Text, nullable=False, server_default=""),
        sa.Column("failed_stage", sa.String(32), nullable=False),
        sa.Column("failure_type", sa.String(32), nullable=False),
        sa.Column("error_message", sa.Text, nullable=False, server_default=""),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["source_configs.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_ingestion_dlq_source", "ingestion_dlq", ["source_id", "created_at"])
    op.create_index("idx_ingestion_dlq_tenant", "ingestion_dlq", ["tenant_id"])

    op.execute("ALTER TABLE ingestion_dlq ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY ingestion_dlq_tenant_isolation ON ingestion_dlq
        USING (tenant_id = current_setting('app.tenant_id', true));
    """)


def downgrade() -> None:
    op.drop_table("ingestion_dlq")
    op.drop_table("ingestion_jobs")
    op.drop_table("source_configs")
