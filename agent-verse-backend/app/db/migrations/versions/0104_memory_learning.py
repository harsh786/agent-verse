"""Create canonical memory lifecycle and governed learning tables.

Revision ID: 0104_memory_learning
Revises: 0103_routing_safety_optimization
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def _timestamps() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def upgrade() -> None:
    op.create_table(
        "memory_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("memory_kind", sa.String(32), nullable=False),
        sa.Column("content_ref", sa.Text(), nullable=False),
        sa.Column("safe_summary", sa.Text(), nullable=False),
        sa.Column("source_goal_id", sa.String(32), nullable=False),
        sa.Column("source_execution_id", sa.String(64), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("classification", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("lifecycle_state", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("embedding_model", sa.String(64), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("outcome_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("effectiveness_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recall_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("helpful_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("harmful_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retention_policy_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("confidence BETWEEN 0 AND 10000", name="ck_memory_confidence"),
        sa.CheckConstraint("embedding_dimension = 1536", name="ck_memory_embedding_dimension"),
        sa.CheckConstraint(
            "recall_count >= 0 AND helpful_count >= 0 AND harmful_count >= 0",
            name="ck_memory_feedback_counts",
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_memory_idempotency"),
    )
    op.create_index(
        "idx_memory_tenant_kind_lifecycle_updated",
        "memory_records",
        ["tenant_id", "memory_kind", "lifecycle_state", "updated_at"],
    )
    op.create_table(
        "memory_feedback",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "memory_id",
            sa.String(32),
            sa.ForeignKey("memory_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column("was_used", sa.Boolean(), nullable=False),
        sa.Column("was_helpful", sa.Boolean(), nullable=False),
        sa.Column("was_harmful", sa.Boolean(), nullable=False),
        sa.Column("outcome_score", sa.Integer(), nullable=False),
        sa.Column("feedback_reason", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "memory_id", "execution_id", name="uq_memory_feedback_execution"
        ),
    )
    op.create_index(
        "idx_memory_feedback_tenant_memory_recorded",
        "memory_feedback",
        ["tenant_id", "memory_id", "recorded_at"],
    )
    op.create_table(
        "prospective_memories",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("intention", sa.Text(), nullable=False),
        sa.Column("trigger_type", sa.String(20), nullable=False),
        sa.Column("trigger_spec", postgresql.JSONB(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("source_goal_id", sa.String(32), nullable=False),
        sa.Column("source_execution_id", sa.String(64), nullable=False),
        sa.Column("policy_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("classification", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_result", postgresql.JSONB(), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_prospective_idempotency"),
    )
    op.create_index(
        "idx_prospective_tenant_state_due",
        "prospective_memories",
        ["tenant_id", "state", "due_at"],
    )
    op.create_table(
        "learning_experiments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("kill_switch", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
    )
    op.create_index(
        "idx_learning_experiments_tenant_status",
        "learning_experiments",
        ["tenant_id", "status", "updated_at"],
    )
    op.create_table(
        "learning_experiment_outcomes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "experiment_id",
            sa.String(64),
            sa.ForeignKey("learning_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assignment_id", sa.String(64), nullable=False),
        sa.Column("arm", sa.String(20), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "assignment_id", name="uq_experiment_assignment_outcome"),
    )
    for table in (
        "memory_records",
        "memory_feedback",
        "prospective_memories",
        "learning_experiments",
        "learning_experiment_outcomes",
    ):
        _rls(table)


def downgrade() -> None:
    for table in (
        "learning_experiment_outcomes",
        "learning_experiments",
        "prospective_memories",
        "memory_feedback",
        "memory_records",
    ):
        op.drop_table(table)
