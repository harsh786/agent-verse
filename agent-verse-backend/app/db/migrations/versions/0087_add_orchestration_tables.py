"""add_orchestration_tables

Revision ID: 0087_orchestration_tables
Revises: 0086_runtime_profile_fields
Create Date: 2026-07-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0087_orchestration_tables"
down_revision = "0086_runtime_profile_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_scorecards",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("goal_id", sa.String(32), nullable=False, index=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("overall_score", sa.Float, nullable=False),
        sa.Column(
            "scores",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("improvement_suggestions", postgresql.JSONB, nullable=True),
        sa.Column("profile_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "tool_trust_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tool_name", sa.String(200), nullable=False, index=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("latency_ms", sa.Float, nullable=False),
        sa.Column("success_rate", sa.Float, nullable=True),
        sa.Column("call_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "self_improvement_actions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("goal_id", sa.String(32), nullable=False, index=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "ab_test_results",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("goal_id", sa.String(32), nullable=False, index=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("experiment_type", sa.String(100), nullable=False, index=True),
        sa.Column("arm_id", sa.String(100), nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "reflexion_lessons",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("lesson", sa.Text, nullable=False),
        sa.Column("source_goal_id", sa.String(32), nullable=False),
        sa.Column(
            "failure_class",
            sa.String(100),
            nullable=False,
            server_default="'unknown'",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("reflexion_lessons")
    op.drop_table("ab_test_results")
    op.drop_table("self_improvement_actions")
    op.drop_table("tool_trust_records")
    op.drop_table("eval_scorecards")
