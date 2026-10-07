"""goal_template_tombstones: a deleted built-in template stays deleted (a10-F230-01)

Built-in goal templates are seeded per tenant once per process
(``INSERT ... ON CONFLICT (id) DO NOTHING``, deterministic ids). The "seeded"
marker lived in process memory, so after a restart or on another replica the
seed ran again and re-inserted every built-in the tenant had deleted. Deleting a
built-in now records a tombstone (same transaction) and seeding skips
tombstoned ids. Tenant-scoped, FORCE RLS.

Revision ID: f5a9c3e7b2d4
Revises: e2b6d4f8a1c3
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f5a9c3e7b2d4"
down_revision: str | Sequence[str] | None = "e2b6d4f8a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "goal_template_tombstones"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            tenant_id    TEXT NOT NULL,
            template_id  TEXT NOT NULL,
            deleted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, template_id)
        )
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.execute(
        f"CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
