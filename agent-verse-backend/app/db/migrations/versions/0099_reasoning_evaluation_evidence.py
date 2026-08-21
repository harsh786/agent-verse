"""Persist versioned reasoning and evaluation evidence.

Revision ID: 0099_reasoning_eval
Revises: 0098_core_execution
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    evaluation_columns = (
        sa.Column("primary_strategy_id", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("primary_strategy_version", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column(
            "auxiliary_strategy_versions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("profile_id", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("strategy_execution_id", sa.Text(), nullable=True),
        sa.Column("evaluator_version", sa.Text(), nullable=False, server_default="eval-runner-v1"),
        sa.Column(
            "evidence_completeness",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("correlation_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("causation_id", sa.Text(), nullable=False, server_default=""),
    )
    for column in evaluation_columns:
        op.add_column("evaluations", column)
    op.execute(
        "UPDATE evaluations SET strategy_execution_id = 'legacy:' || id "
        "WHERE strategy_execution_id IS NULL"
    )
    op.alter_column("evaluations", "strategy_execution_id", nullable=False)
    op.create_unique_constraint(
        "uq_evaluations_versioned_execution",
        "evaluations",
        ["tenant_id", "goal_id", "strategy_execution_id", "evaluator_version"],
    )
    op.create_index(
        "ix_evaluations_tenant_strategy_created",
        "evaluations",
        ["tenant_id", "primary_strategy_id", "created_at"],
    )

    scorecard_columns = (
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("primary_strategy_id", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("primary_strategy_version", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column(
            "auxiliary_strategy_versions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("strategy_execution_id", sa.Text(), nullable=True),
        sa.Column(
            "evaluator_version",
            sa.Text(),
            nullable=False,
            server_default="runtime-scorecard-v2",
        ),
        sa.Column(
            "dimension_status",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text('\'{"legacy": "legacy_unknown"}\'::jsonb'),
        ),
        sa.Column(
            "evidence_references",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("coverage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("correlation_id", sa.Text(), nullable=False, server_default=""),
    )
    for column in scorecard_columns:
        op.add_column("eval_scorecards", column)
    op.execute(
        "UPDATE eval_scorecards SET strategy_execution_id = 'legacy:' || id "
        "WHERE strategy_execution_id IS NULL"
    )
    op.alter_column("eval_scorecards", "strategy_execution_id", nullable=False)
    op.create_unique_constraint(
        "uq_eval_scorecards_versioned_execution",
        "eval_scorecards",
        ["tenant_id", "goal_id", "strategy_execution_id", "evaluator_version"],
    )
    op.create_index(
        "ix_eval_scorecards_tenant_strategy_created",
        "eval_scorecards",
        ["tenant_id", "primary_strategy_id", "created_at"],
    )

    op.create_table(
        "regression_cases",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("goal_id", sa.String(32), nullable=False),
        sa.Column("strategy_id", sa.Text(), nullable=False),
        sa.Column("strategy_version", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("evaluator_version", sa.Text(), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False, server_default="default-v1"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "goal_id",
            "strategy_id",
            "strategy_version",
            "profile_version",
            "evaluator_version",
            name="uq_regression_case_versioned",
        ),
    )
    op.create_index(
        "ix_regression_cases_tenant_strategy_created",
        "regression_cases",
        ["tenant_id", "strategy_id", "created_at"],
    )

    op.create_table(
        "regression_baselines",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("cohort", sa.Text(), nullable=False),
        sa.Column("strategy_id", sa.Text(), nullable=False),
        sa.Column("strategy_version", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("evaluator_version", sa.Text(), nullable=False),
        sa.Column("eval_suite_version", sa.Text(), nullable=False),
        sa.Column("limits_policy_version", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "cohort",
            "strategy_id",
            "strategy_version",
            "profile_version",
            "evaluator_version",
            "eval_suite_version",
            "limits_policy_version",
            "revision",
            name="uq_regression_baseline_revision",
        ),
    )
    op.create_index(
        "ix_regression_baselines_tenant_strategy_revision",
        "regression_baselines",
        ["tenant_id", "strategy_id", "revision"],
    )

    op.create_table(
        "reasoning_promotion_decisions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("baseline_id", sa.String(32), nullable=False),
        sa.Column("strategy_id", sa.Text(), nullable=False),
        sa.Column("strategy_version", sa.Text(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("metric_deltas", postgresql.JSONB(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_reasoning_promotion_tenant_strategy_created",
        "reasoning_promotion_decisions",
        ["tenant_id", "strategy_id", "created_at"],
    )

    for table in (
        "evaluations",
        "eval_scorecards",
        "regression_cases",
        "regression_baselines",
        "reasoning_promotion_decisions",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
        )


def downgrade() -> None:
    # Tolerate an earlier local development shape of this uncommitted revision.
    op.execute("DROP TABLE IF EXISTS reasoning_promotion_decisions")
    op.execute("DROP TABLE IF EXISTS regression_baselines")
    op.drop_table("regression_cases")
    op.drop_index("ix_eval_scorecards_tenant_strategy_created", table_name="eval_scorecards")
    op.drop_constraint("uq_eval_scorecards_versioned_execution", "eval_scorecards", type_="unique")
    for name in (
        "correlation_id",
        "coverage",
        "evidence_references",
        "dimension_status",
        "evaluator_version",
        "strategy_execution_id",
        "auxiliary_strategy_versions",
        "primary_strategy_version",
        "primary_strategy_id",
        "profile_version",
    ):
        op.drop_column("eval_scorecards", name)
    op.drop_index("ix_evaluations_tenant_strategy_created", table_name="evaluations")
    op.drop_constraint("uq_evaluations_versioned_execution", "evaluations", type_="unique")
    for name in (
        "causation_id",
        "correlation_id",
        "evidence_completeness",
        "evaluator_version",
        "strategy_execution_id",
        "profile_version",
        "profile_id",
        "auxiliary_strategy_versions",
        "primary_strategy_version",
        "primary_strategy_id",
    ):
        op.drop_column("evaluations", name)
