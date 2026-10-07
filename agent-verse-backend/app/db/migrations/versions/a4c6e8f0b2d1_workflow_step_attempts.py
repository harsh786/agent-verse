"""Workflow step retry attempts and the run's structured failure.

A step's retry policy (``retry.max_attempts`` + backoff) runs it several times in
one execution, but nothing recorded that: the steps API showed one row with the
final status, a skipped step's output was null and the error that caused the
skip / abort lived only in a free-text column.

* ``workflow_step_results.attempts`` — how many times the retry policy ran the
  step (NULL on older rows means 1).
* ``workflow_step_results.attempt_log`` — each failed attempt: error, error type,
  classification (``error_kind`` / ``retryable``), error id, duration and the
  backoff before the next attempt.
* ``workflow_runs.error_detail`` — the failing step's final error, its
  classification, attempt count, error id and why it stopped
  (``non_retryable`` / ``retries_exhausted``).

Additive: nullable columns, no table rewrite.

Revision ID: a4c6e8f0b2d1
Revises: f3b5d7e9a1c4
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a4c6e8f0b2d1"
down_revision: str | None = "f3b5d7e9a1c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE workflow_step_results ADD COLUMN IF NOT EXISTS attempts INTEGER")
    op.execute("ALTER TABLE workflow_step_results ADD COLUMN IF NOT EXISTS attempt_log JSONB")
    op.execute("ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS error_detail JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE workflow_runs DROP COLUMN IF EXISTS error_detail")
    op.execute("ALTER TABLE workflow_step_results DROP COLUMN IF EXISTS attempt_log")
    op.execute("ALTER TABLE workflow_step_results DROP COLUMN IF EXISTS attempts")
