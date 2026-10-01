"""skill_runtime_tenant_state: durable per-tenant skill enable/disable (OPS-03)

The skills-runtime disable set and the ``_enabled_skills`` map were
process-local dicts: a tenant-disabled skill kept running on other replicas and
after a restart. One tenant-scoped (FORCE RLS) row per (tenant, skill) is now
the single source both enable/disable APIs write and every execute path reads.
No row means the skill has not been toggled (allowed).

Revision ID: c83e5a7b9d42
Revises: 4075a45ac5a5
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c83e5a7b9d42"
down_revision: str | None = "4075a45ac5a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "skill_runtime_tenant_state"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("skill_id", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "skill_id"),
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.drop_table(_TABLE)
