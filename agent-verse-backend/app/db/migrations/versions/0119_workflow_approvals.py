"""Cross-process workflow HITL approval store (gap #2).

Adds ``workflow_approvals`` — a durable, tenant-scoped (RLS) document store for
:class:`~app.workflow.hitl_extension.WorkflowHITLRequest`. Until now the
``HITLWorkflowGateway`` kept pending approvals only in an in-memory per-process
dict (with an optional best-effort Redis mirror), so a workflow suspended in an
out-of-process Celery worker created its approval in the *worker's* memory —
invisible to the API's ``/approvals`` endpoints — and an API-side decision never
reached the worker. This table makes a pending approval created by the worker
visible to the API, and a decision made via the API visible to the worker.

The full ``WorkflowHITLRequest`` dataclass is stored as a JSONB ``payload`` so
the round-trip is loss-free regardless of the (string-shaped) run/step/user ids
the gateway uses; a handful of scalar columns are lifted out for indexed
filtering (tenant listing, assignee inbox, pending queue).

Distinct from ``workflow_hitl_requests`` (migration 0108), whose strict
UUID-typed columns and NOT-NULL assignee/deadline never matched the gateway's
string-id, optional-assignee dataclass and so were never written to.

Idempotent (``IF NOT EXISTS`` / ``DROP POLICY IF EXISTS``), mirroring the
0117/0118 style.

Revision ID: 0119
Revises: 0118
"""

from __future__ import annotations

from alembic import op

revision = "0119"
down_revision = "0118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_approvals (
            request_id   TEXT PRIMARY KEY,
            tenant_id    UUID NOT NULL,
            run_id       TEXT NOT NULL,
            workflow_id  TEXT,
            step_id      TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending',
            priority     TEXT NOT NULL DEFAULT 'medium',
            assigned_to  TEXT,
            payload      JSONB NOT NULL DEFAULT '{}',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("ALTER TABLE workflow_approvals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_approvals FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS workflow_approvals_tenant ON workflow_approvals")
    op.execute("""
        CREATE POLICY workflow_approvals_tenant ON workflow_approvals
        USING (tenant_id::text = current_setting('app.tenant_id', TRUE))
        WITH CHECK (tenant_id::text = current_setting('app.tenant_id', TRUE))
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_wf_approvals_pending
        ON workflow_approvals (tenant_id, status, priority, created_at)
        WHERE status = 'pending'
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_wf_approvals_assignee
        ON workflow_approvals (tenant_id, assigned_to, status)
        WHERE assigned_to IS NOT NULL
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_wf_approvals_run ON workflow_approvals (run_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workflow_approvals CASCADE")
