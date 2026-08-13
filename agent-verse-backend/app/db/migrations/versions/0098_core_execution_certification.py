"""Extend goal attempts with versioned core-execution evidence.

Revision ID: 0098_core_execution
Revises: 0097_coordination_runtime
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0098_core_execution"
down_revision = "0097_coordination_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("goal_attempts", sa.Column("strategy_execution_id", sa.Text()))
    op.add_column(
        "goal_attempts",
        sa.Column("strategy_id", sa.Text(), nullable=False, server_default="react"),
    )
    op.add_column(
        "goal_attempts",
        sa.Column("strategy_version", sa.Text(), nullable=False, server_default="1.0.0"),
    )
    op.add_column(
        "goal_attempts",
        sa.Column("profile_id", sa.Text(), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "goal_attempts",
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "goal_attempts",
        sa.Column("transition_reason", sa.Text(), nullable=False, server_default="started"),
    )
    op.add_column("goal_attempts", sa.Column("checkpoint_reference", sa.Text()))
    op.add_column(
        "goal_attempts",
        sa.Column(
            "budget_consumed",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "goal_attempts",
        sa.Column(
            "terminal_evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "goal_attempts",
        sa.Column("idempotency_key", sa.Text(), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "goal_attempts",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_unique_constraint(
        "uq_goal_attempts_tenant_goal_attempt",
        "goal_attempts",
        ["tenant_id", "goal_id", "attempt_number"],
    )
    op.create_unique_constraint(
        "uq_goal_attempts_tenant_idempotency",
        "goal_attempts",
        ["tenant_id", "idempotency_key"],
    )
    op.create_index(
        "ix_goal_attempts_tenant_goal_started",
        "goal_attempts",
        ["tenant_id", "goal_id", "started_at"],
    )
    op.execute("DROP POLICY IF EXISTS goal_attempts_tenant_isolation ON goal_attempts")
    op.execute(
        "CREATE POLICY goal_attempts_tenant_isolation ON goal_attempts "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS goal_attempts_tenant_isolation ON goal_attempts")
    op.execute(
        "CREATE POLICY goal_attempts_tenant_isolation ON goal_attempts "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    op.drop_index("ix_goal_attempts_tenant_goal_started", table_name="goal_attempts")
    op.drop_constraint(
        "uq_goal_attempts_tenant_idempotency", "goal_attempts", type_="unique"
    )
    op.drop_constraint(
        "uq_goal_attempts_tenant_goal_attempt", "goal_attempts", type_="unique"
    )
    for column in (
        "version",
        "idempotency_key",
        "terminal_evidence",
        "budget_consumed",
        "checkpoint_reference",
        "transition_reason",
        "profile_version",
        "profile_id",
        "strategy_version",
        "strategy_id",
        "strategy_execution_id",
    ):
        op.drop_column("goal_attempts", column)
