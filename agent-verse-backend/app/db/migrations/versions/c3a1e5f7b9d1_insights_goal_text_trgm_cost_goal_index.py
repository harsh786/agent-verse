"""goals.goal_text trigram index + goal_cost_breakdowns(goal_id) (a10-F233-02/03)

``POST /insights/estimate`` finds the tenant's similar past goals with pg_trgm.
It filtered on ``similarity(goal_text, :goal) >= 0.2``, a function call no index
can serve, so every estimate scored every goal of the tenant's 180-day window.
The statement now uses the ``%`` operator (threshold set per transaction), which
this GIN ``gin_trgm_ops`` index serves.

Per-goal cost is now a correlated lookup of only the goals a query returns
instead of a ``GROUP BY`` over the whole ledger. Tenant reads hit the primary
key ``(tenant_id, goal_id, ...)``; the cross-tenant benchmark has no UUID tenant
to match (``goals.tenant_id`` is text), so it needs ``(goal_id)``.

Built CONCURRENTLY (outside the migration transaction) so the tables are not
write-locked during the build.

Revision ID: c3a1e5f7b9d1
Revises: a7c9e1f3b5d7
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c3a1e5f7b9d1"
down_revision: str | Sequence[str] | None = "a7c9e1f3b5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_goals_goal_text_trgm "
            "ON goals USING gin (goal_text gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_goal_cost_breakdowns_goal_id "
            "ON goal_cost_breakdowns (goal_id)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_goal_cost_breakdowns_goal_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_goals_goal_text_trgm")
