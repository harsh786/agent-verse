"""Goal runner heartbeat (GOAL-STALL).

A worker that died mid-goal (SIGABRT / OOM / host sleep) left its goal
``executing`` with no events: the redelivered task found the dead runner's Redis
lock and skipped, and the only reaper waited out the plan's goal timeout (1-24 h).

* ``goals.heartbeat_at`` — written by the running worker every
  ``GOAL_HEARTBEAT_INTERVAL_SECONDS``;
* ``goals.runner_token`` — the run's lock token, so the reaper can release the
  dead runner's Redis lock and only that one;
* a partial index over active goals' heartbeats for the beat reaper's scan.

Revision ID: e4f7a2c9d1b8
Revises: c3d9e1f7a2b8
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e4f7a2c9d1b8"
down_revision: str | None = "c3d9e1f7a2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE goals ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ")
    op.execute("ALTER TABLE goals ADD COLUMN IF NOT EXISTS runner_token TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_goals_active_heartbeat ON goals (heartbeat_at) "
        "WHERE status IN ('executing', 'planning') AND heartbeat_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_goals_active_heartbeat")
    op.execute("ALTER TABLE goals DROP COLUMN IF EXISTS runner_token")
    op.execute("ALTER TABLE goals DROP COLUMN IF EXISTS heartbeat_at")
