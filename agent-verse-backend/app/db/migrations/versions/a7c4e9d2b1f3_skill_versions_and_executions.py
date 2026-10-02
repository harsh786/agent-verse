"""skill_versions + skill_executions: durable skills-runtime history (OPS-04)

Skill execution history and archived skill versions lived in module dicts:
per replica, lost on restart, and versions were keyed by skill_id only (no
tenant). Both are now tenant-scoped (FORCE RLS) tables read newest-first with
keyset pagination on (tenant_id, skill_id, <timestamp> DESC).

Revision ID: a7c4e9d2b1f3
Revises: cf87de8eae52
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c4e9d2b1f3"
down_revision: str | None = "cf87de8eae52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("skill_versions", "skill_executions")


def _force_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def upgrade() -> None:
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("skill_id", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_skill_versions_tenant_skill_archived",
        "skill_versions",
        ["tenant_id", "skill_id", sa.text("archived_at DESC"), "id"],
    )
    op.create_table(
        "skill_executions",
        sa.Column("execution_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("skill_id", sa.String(128), nullable=False),
        sa.Column("skill_name", sa.String(256), nullable=True),
        sa.Column("goal_id", sa.String(64), nullable=True),
        sa.Column("input_preview", sa.Text(), nullable=False, server_default=""),
        sa.Column("output_preview", sa.Text(), nullable=False, server_default=""),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("model_used", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_skill_executions_tenant_skill_created",
        "skill_executions",
        ["tenant_id", "skill_id", sa.text("created_at DESC"), "execution_id"],
    )
    for table in _TABLES:
        _force_rls(table)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_table("skill_executions")
    op.drop_table("skill_versions")
