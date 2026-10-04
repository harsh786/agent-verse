"""MEM-53 (completion): non-blocking eval-suite steps poll golden goals.

A worker step no longer sits on a Celery slot waiting for a golden goal (the
goals need worker slots themselves). Rows move
``pending -> submitting -> waiting -> done``:

* ``deadline_at`` — when a ``waiting`` task's goal is cancelled and the task
  reported timed out;
* ``next_check_at`` — when the goal's status is next polled; the partial index
  serves the per-run "due waiting tasks" claim.

Rows a previous version left ``running`` (a blocking worker owned them) become
``waiting`` when they have a goal, else ``pending``, so an upgraded worker
resumes them.

Revision ID: b53e9d1f7a24
Revises: f1e790b4050a
"""

from __future__ import annotations

from alembic import op

revision = "b53e9d1f7a24"
down_revision = "f1e790b4050a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE eval_suite_task_results "
        "ADD COLUMN IF NOT EXISTS deadline_at TIMESTAMPTZ, "
        "ADD COLUMN IF NOT EXISTS next_check_at TIMESTAMPTZ"
    )
    op.execute(
        "UPDATE eval_suite_task_results SET state = 'waiting', lease_owner = NULL, "
        " lease_expires_at = NULL, next_check_at = now(), "
        " deadline_at = now() + interval '30 minutes' "
        "WHERE state = 'running' AND goal_id IS NOT NULL"
    )
    op.execute(
        "UPDATE eval_suite_task_results SET state = 'pending', lease_owner = NULL, "
        " lease_expires_at = NULL "
        "WHERE state = 'running' AND goal_id IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eval_suite_task_results_due "
        "ON eval_suite_task_results (tenant_id, run_id, next_check_at) WHERE state = 'waiting'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_eval_suite_task_results_due")
    op.execute(
        "UPDATE eval_suite_task_results SET state = 'running' "
        "WHERE state IN ('submitting', 'waiting')"
    )
    op.execute(
        "ALTER TABLE eval_suite_task_results "
        "DROP COLUMN IF EXISTS next_check_at, DROP COLUMN IF EXISTS deadline_at"
    )
