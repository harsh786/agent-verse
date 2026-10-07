"""goal_fanout_ledger: per-child timeout + deadline (a01-F006-05 / a01-F007-01).

Fan-out children are real goals and the parent no longer waits on them in its
worker slot: it parks in ``waiting_children`` and the last child to finish
re-queues it. The fixed 300 s in-slot wait per sub-task is replaced by a
per-child timeout (``timeout_s``) recorded when the child is dispatched, with
its ``deadline_at``. The fan-out sweeper (beat) cancels a child still running
past its deadline; time a child spends waiting for a human pushes it out.

``ix_goal_fanout_ledger_deadline`` keeps the sweeper's scan on in-flight rows.

Revision ID: e4c8a2f6d9b1
Revises: a7c9e1f3b5d7
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e4c8a2f6d9b1"
down_revision: str | Sequence[str] | None = "a7c9e1f3b5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE goal_fanout_ledger ADD COLUMN IF NOT EXISTS timeout_s INTEGER")
    op.execute("ALTER TABLE goal_fanout_ledger ADD COLUMN IF NOT EXISTS deadline_at TIMESTAMPTZ")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_goal_fanout_ledger_deadline "
        "ON goal_fanout_ledger (deadline_at) "
        "WHERE status = 'dispatched' AND deadline_at IS NOT NULL"
    )
    # The sweeper's safety-net wake scans parked parents.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_goals_waiting_children "
        "ON goals (tenant_id) WHERE status = 'waiting_children'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_goals_waiting_children")
    op.execute("DROP INDEX IF EXISTS ix_goal_fanout_ledger_deadline")
    op.execute("ALTER TABLE goal_fanout_ledger DROP COLUMN IF EXISTS deadline_at")
    op.execute("ALTER TABLE goal_fanout_ledger DROP COLUMN IF EXISTS timeout_s")
