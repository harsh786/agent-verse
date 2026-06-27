"""Create eval_suites and eval_suite_results tables.

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_suites",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("tasks", sa.JSON, nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
    )
    op.create_index("ix_eval_suites_tenant", "eval_suites", ["tenant_id"])

    op.create_table(
        "eval_suite_results",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("suite_id", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("total_tasks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("passed_tasks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_tasks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pass_rate", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("task_results", sa.JSON, nullable=False, server_default="[]"),
        sa.Column(
            "run_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
    )
    op.create_index("ix_eval_suite_results_suite", "eval_suite_results", ["suite_id"])
    op.create_index("ix_eval_suite_results_tenant", "eval_suite_results", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_eval_suite_results_tenant", "eval_suite_results")
    op.drop_index("ix_eval_suite_results_suite", "eval_suite_results")
    op.drop_table("eval_suite_results")
    op.drop_index("ix_eval_suites_tenant", "eval_suites")
    op.drop_table("eval_suites")
