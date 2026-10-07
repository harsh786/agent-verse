"""workflow_approvals.deadline_at / timeout_handled_at: an indexed SLA sweep.

The approval SLA sweep (``workflow.check_hitl_escalations``) read the 500 OLDEST
pending approvals and parsed each JSON payload's deadline in Python, so a short
gate created behind 500 long-lived ones was never looked at, and every sweep
read the whole pending set. The deadline is now a column (created_at + the
step's timeout; set by ``PostgresWorkflowApprovalStore.save``) and
``timeout_handled_at`` records that the request's ``timeout_action`` was applied
(exactly once). The sweep is one range scan of the partial index
``ix_workflow_approvals_pending_deadline``, most overdue first, bounded per run.

Backfill (pending rows only): the payload's ``deadline_at``, else ``created_at``
+ ``escalation_after_hours`` (how the sweep treated deadline-less rows); a row
the old sweep already escalated (discussion entry by ``system:sla``) is marked
handled so it is not escalated a second time.

Additive: two nullable columns (no table rewrite); the index is built
CONCURRENTLY (no write lock).

Revision ID: f3b5d7e9a1c4
Revises: e1f3a5c7b9d2
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f3b5d7e9a1c4"
down_revision: str | Sequence[str] | None = "e1f3a5c7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_workflow_approvals_pending_deadline"


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s';")
    op.execute(
        "ALTER TABLE workflow_approvals ADD COLUMN IF NOT EXISTS deadline_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE workflow_approvals ADD COLUMN IF NOT EXISTS timeout_handled_at TIMESTAMPTZ"
    )
    op.execute(
        r"""
        UPDATE workflow_approvals SET deadline_at = CASE
            WHEN payload->>'deadline_at' ~ '^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}'
                THEN CAST(payload->>'deadline_at' AS timestamptz)
            WHEN payload->>'escalation_after_hours' ~ '^\d+(\.\d+)?$'
                 AND CAST(payload->>'escalation_after_hours' AS float8) > 0
                THEN created_at + make_interval(
                    secs => CAST(payload->>'escalation_after_hours' AS float8) * 3600)
        END
        WHERE status = 'pending' AND deadline_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE workflow_approvals SET timeout_handled_at = updated_at
        WHERE status = 'pending' AND timeout_handled_at IS NULL
          AND payload->'discussion' @> '[{"type": "escalation", "by": "system:sla"}]'::jsonb
        """
    )
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX} "
            "ON workflow_approvals (deadline_at) "
            "WHERE status = 'pending' AND timeout_handled_at IS NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
    op.execute("ALTER TABLE workflow_approvals DROP COLUMN IF EXISTS timeout_handled_at")
    op.execute("ALTER TABLE workflow_approvals DROP COLUMN IF EXISTS deadline_at")
