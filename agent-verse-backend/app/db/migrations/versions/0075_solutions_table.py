"""Add solutions table for Phase 7 — packaged agent deployments.

Revision ID: 0075
Revises: 0074
Create Date: 2026-07-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "solutions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("domain", sa.String(64), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("version", sa.String(32), nullable=False, server_default="1.0.0"),
        sa.Column("agents_config", postgresql.JSON, nullable=False, server_default="[]"),
        sa.Column("knowledge_recipes", postgresql.JSON, nullable=False, server_default="[]"),
        sa.Column("workflows_config", postgresql.JSON, nullable=False, server_default="[]"),
        sa.Column("schedules_config", postgresql.JSON, nullable=False, server_default="[]"),
        sa.Column("policies_config", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("eval_suite_id", sa.String(32), nullable=True),
        sa.Column("onboarding_steps", postgresql.JSON, nullable=False, server_default="[]"),
        sa.Column("visibility", sa.String(32), nullable=False, server_default="public"),
        sa.Column("install_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_solutions_domain", "solutions", ["domain"])
    op.create_index("ix_solutions_slug", "solutions", ["slug"])


def downgrade() -> None:
    op.drop_table("solutions")
