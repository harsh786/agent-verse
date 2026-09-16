"""hot composite indexes goals goal_events usage

Adds the composite indexes the hot analytics/list/replay paths sort and filter by
but which were missing, forcing per-request scans+sorts of a tenant's rows at
scale (distributed-scale audit X12):
  * goals(tenant_id, created_at DESC)      — every date-range analytics/list query
  * goal_events(goal_id, created_at)       — per-goal event time-range aggregation
  * usage_records(tenant_id, period_start) — billing rollups

Postgres uses these automatically for the existing WHERE/ORDER BY — no query
change needed. (The long_term_memory vector ANN index is handled separately since
it also needs a halfvec query cast.)

Revision ID: 5bb611225952
Revises: e9ba31bb5029
Create Date: 2026-09-16 11:55:52.749240
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "5bb611225952"
down_revision: str | None = "e9ba31bb5029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_goals_tenant_created "
        "ON goals (tenant_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_goal_events_goal_created "
        "ON goal_events (goal_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_usage_records_tenant_period "
        "ON usage_records (tenant_id, period_start)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_usage_records_tenant_period")
    op.execute("DROP INDEX IF EXISTS ix_goal_events_goal_created")
    op.execute("DROP INDEX IF EXISTS ix_goals_tenant_created")
