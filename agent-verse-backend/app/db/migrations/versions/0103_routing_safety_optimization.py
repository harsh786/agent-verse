"""Persist canonical routing decisions and optimization outcomes.

Revision ID: 0103_routing_safety_optimization
Revises: 0102_camel_generative_swarm_auction
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0103_routing_safety_optimization"
down_revision = "0102_camel_generative_swarm_auction"
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


def upgrade() -> None:
    op.create_table(
        "routing_decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("goal_id", sa.String(32), nullable=False),
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("selected_candidate_id", sa.Text(), nullable=True),
        sa.Column("selected_candidate_version", sa.Text(), nullable=True),
        sa.Column("safe_rationale", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "category IN ('model','skill','tool','embedding','strategy')",
            name="ck_routing_decision_category",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "execution_id",
            "category",
            "id",
            name="uq_routing_decision_execution_category_id",
        ),
    )
    op.create_index(
        "idx_routing_decisions_tenant_goal_created",
        "routing_decisions",
        ["tenant_id", "goal_id", "created_at"],
    )
    op.create_index(
        "idx_routing_decisions_tenant_category_created",
        "routing_decisions",
        ["tenant_id", "category", "created_at"],
    )
    op.create_table(
        "routing_outcomes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "decision_id",
            sa.String(64),
            sa.ForeignKey("routing_decisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("evaluator_version", sa.String(64), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("quality_score", sa.Integer(), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("actual_latency_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("attempt > 0", name="ck_routing_outcome_attempt"),
        sa.CheckConstraint("quality_score BETWEEN 0 AND 10000", name="ck_routing_outcome_quality"),
        sa.CheckConstraint("actual_cost_usd >= 0", name="ck_routing_outcome_cost"),
        sa.CheckConstraint("actual_latency_ms >= 0", name="ck_routing_outcome_latency"),
        sa.CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0", name="ck_routing_outcome_tokens"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "decision_id",
            "attempt",
            "evaluator_version",
            name="uq_routing_outcome_attempt_evaluator",
        ),
    )
    op.create_index(
        "idx_routing_outcomes_tenant_decision_recorded",
        "routing_outcomes",
        ["tenant_id", "decision_id", "recorded_at"],
    )
    _rls("routing_decisions")
    _rls("routing_outcomes")


def downgrade() -> None:
    op.drop_table("routing_outcomes")
    op.drop_table("routing_decisions")
