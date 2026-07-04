"""Add skills table for Phase 6 Skills Platform.

Revision ID: 0074
Revises: 0073
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=False, server_default="1.0.0"),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "trigger_hints",
            postgresql.JSON,
            nullable=False,
            server_default="[]",
        ),
        sa.Column("instructions", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "few_shot_examples",
            postgresql.JSON,
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "allowed_tools", postgresql.JSON, nullable=False, server_default="[]"
        ),
        sa.Column(
            "required_connectors",
            postgresql.JSON,
            nullable=False,
            server_default="[]",
        ),
        sa.Column("token_estimate", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "visibility", sa.String(32), nullable=False, server_default="tenant"
        ),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default="true"
        ),
        sa.Column("created_by", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_skills_tenant", "skills", ["tenant_id"])
    op.create_index("ix_skills_visibility", "skills", ["visibility"])

    # RLS: tenant skills are isolated; platform skills (tenant_id=NULL) are visible to all
    op.execute("ALTER TABLE skills ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE skills FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY skill_access ON skills
        USING (
            tenant_id IS NULL  -- platform skills visible to everyone
            OR tenant_id = current_setting('app.tenant_id', true)
        )
        WITH CHECK (
            tenant_id = current_setting('app.tenant_id', true)
        )
    """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS skill_access ON skills")
    op.drop_table("skills")
