"""Durable, resumable eval-suite runs: one leased result row per golden task (MEM-53).

A suite run was an asyncio task on the API replica that received the request,
with fixed concurrency and results written only at the very end: a deploy lost
the whole run, large datasets could not complete, and a run older than an hour
was reported "abandoned" while it was still executing.

* ``eval_suite_task_results`` — one row per (run, task), enqueued when the run
  starts (``INSERT ... SELECT`` from the dataset version), moving
  ``pending -> submitting -> waiting -> done``. Short, non-blocking worker
  steps claim rows with ``FOR UPDATE SKIP LOCKED`` under a lease, record the
  goal id (with a ``deadline_at``) once the golden goal is submitted, and poll
  due ``waiting`` rows (``next_check_at``) until their goal is terminal. A step
  that dies leaves a lease that expires; the row is claimed again and a
  recorded goal is polled, never resubmitted.
* ``eval_suite_results.last_progress_at`` — heartbeat; "abandoned" is derived
  from it, not from ``run_at``. ``tenant_plan`` lets the stalled-run sweeper
  rebuild the tenant context; ``concurrency`` is the run's worker count.

Revision ID: e7a1c4d2b9f2
Revises: a4d51be05bf1
"""

from __future__ import annotations

from alembic import op

revision = "e7a1c4d2b9f2"
down_revision = "a4d51be05bf1"
branch_labels = None
depends_on = None

_POLICY = (
    "CREATE POLICY eval_suite_task_results_tenant_isolation ON eval_suite_task_results "
    "AS PERMISSIVE FOR ALL "
    "USING (tenant_id = current_setting('app.tenant_id', true)) "
    "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS eval_suite_task_results (
            tenant_id        VARCHAR(64) NOT NULL,
            run_id           VARCHAR(32) NOT NULL,
            task_id          TEXT        NOT NULL,
            suite_id         VARCHAR(32) NOT NULL,
            ordinal          BIGINT      NOT NULL,
            task             JSONB       NOT NULL,
            state            VARCHAR(16) NOT NULL DEFAULT 'pending',
            attempts         INTEGER     NOT NULL DEFAULT 0,
            lease_owner      TEXT,
            lease_expires_at TIMESTAMPTZ,
            goal_id          VARCHAR(64),
            deadline_at      TIMESTAMPTZ,
            next_check_at    TIMESTAMPTZ,
            status           VARCHAR(16),
            passed           BOOLEAN,
            score            DOUBLE PRECISION,
            terminal_event   VARCHAR(32),
            failure_reasons  JSONB NOT NULL DEFAULT '[]'::jsonb,
            judge            JSONB,
            duration_seconds DOUBLE PRECISION,
            started_at       TIMESTAMPTZ,
            finished_at      TIMESTAMPTZ,
            PRIMARY KEY (tenant_id, run_id, task_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eval_suite_task_results_claim "
        "ON eval_suite_task_results (tenant_id, run_id, state, ordinal)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eval_suite_task_results_due "
        "ON eval_suite_task_results (tenant_id, run_id, next_check_at) WHERE state = 'waiting'"
    )
    op.execute("ALTER TABLE eval_suite_task_results ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eval_suite_task_results FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS eval_suite_task_results_tenant_isolation "
        "ON eval_suite_task_results"
    )
    op.execute(_POLICY)
    op.execute(
        "ALTER TABLE eval_suite_results "
        "ADD COLUMN IF NOT EXISTS last_progress_at TIMESTAMPTZ, "
        "ADD COLUMN IF NOT EXISTS tenant_plan VARCHAR(32), "
        "ADD COLUMN IF NOT EXISTS concurrency INTEGER"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eval_suite_results_running "
        "ON eval_suite_results (status, last_progress_at) WHERE status = 'running'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_eval_suite_results_running")
    op.execute(
        "ALTER TABLE eval_suite_results DROP COLUMN IF EXISTS concurrency, "
        "DROP COLUMN IF EXISTS tenant_plan, DROP COLUMN IF EXISTS last_progress_at"
    )
    op.execute("DROP TABLE IF EXISTS eval_suite_task_results")
