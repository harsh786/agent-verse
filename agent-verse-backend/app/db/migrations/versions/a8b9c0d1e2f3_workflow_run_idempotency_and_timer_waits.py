"""workflow_runs: idempotency key (unique per tenant+workflow) + durable timer waits.

* ``idempotency_key`` — ``POST /workflows/{id}/trigger`` accepted an
  ``idempotency_key`` but only stashed it in (never-written) ``run_metadata``, so
  a client retrying a trigger after a network blip started a second run. The
  partial UNIQUE index makes the database the arbiter: a second insert with the
  same (tenant_id, workflow_id, idempotency_key) conflicts and the runner returns
  the existing run instead — correct across API replicas, not just in-process.
* ``wake_at`` — a ``wait`` step with a timer used to ``asyncio.sleep`` (capped at
  300 s, while reporting the full requested duration) inside the Celery task,
  holding a worker slot. A durable timer wait now persists the wake time, ends the
  task with the run in ``waiting_timer``, and a beat scan re-dispatches the run
  once ``wake_at`` has passed. The partial index keeps that scan cheap.

Revision ID: a8b9c0d1e2f3
Revises: e4b7c1d9a2f3
"""

from __future__ import annotations

from alembic import op

revision = "a8b9c0d1e2f3"
down_revision = "e4b7c1d9a2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS idempotency_key TEXT")
    op.execute("ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS wake_at TIMESTAMPTZ")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_runs_idempotency "
        "ON workflow_runs (tenant_id, workflow_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_runs_timer_wake "
        "ON workflow_runs (wake_at) WHERE status = 'waiting_timer'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_workflow_runs_timer_wake")
    op.execute("DROP INDEX IF EXISTS uq_workflow_runs_idempotency")
    op.execute("ALTER TABLE workflow_runs DROP COLUMN IF EXISTS wake_at")
    op.execute("ALTER TABLE workflow_runs DROP COLUMN IF EXISTS idempotency_key")
