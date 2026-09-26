"""agent_grants — cumulative spend column and the delegation-chain index.

``Grant.max_cost_usd`` was advertised (API, model docstring) as the spend a grant
authorises, but ``Grant.covers()`` compared a *single* call's cost to it, so a
$10 grant permitted unbounded spend in $9.99 increments. Worse, the only
enforcement site in the product — ``executor_mixin``'s tool gate — called
``enforce_tool_call`` without a ``cost_usd`` at all, so the comparison was always
``0.0 > cap`` and the field never affected anything. ``spent_usd`` turns it into
a real budget, incremented atomically in SQL so concurrent executors on
different replicas cannot lose each other's charges.

``ix_agent_grants_tenant_parent`` serves the recursive revocation walk added in
the same change: revoking a grant now revokes everything delegated from it,
transitively, which joins ``parent_grant_id`` back to ``grant_id``.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""

from __future__ import annotations

from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_grants ADD COLUMN IF NOT EXISTS "
        "spent_usd DOUBLE PRECISION NOT NULL DEFAULT 0"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_grants_tenant_parent "
        "ON agent_grants (tenant_id, parent_grant_id) "
        "WHERE parent_grant_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_grants_tenant_parent")
    op.execute("ALTER TABLE agent_grants DROP COLUMN IF EXISTS spent_usd")
