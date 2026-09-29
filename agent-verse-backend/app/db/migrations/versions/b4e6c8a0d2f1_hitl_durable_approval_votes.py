"""Durable multi-approver HITL votes.

``HITLGateway`` counted approver votes in process memory and never persisted a
request's ``required_approvers``: a two-person approval split across replicas
(or across a restart) never reached its threshold — or, re-read from the DB
with the default threshold of 1, was released by a single vote.

* ``approval_requests.required_approvers`` — the threshold, persisted with the
  request so every replica applies the same one.
* ``approval_votes`` — one row per (request, distinct approver); the vote count
  is ``COUNT(*)`` under the tenant's RLS context. Tenant-isolated by FORCE'd RLS.

Revision ID: b4e6c8a0d2f1
Revises: a9c4e2f7b1d3
"""

from __future__ import annotations

from alembic import op

revision = "b4e6c8a0d2f1"
down_revision = "a9c4e2f7b1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE approval_requests "
        "ADD COLUMN IF NOT EXISTS required_approvers INTEGER NOT NULL DEFAULT 1"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_votes (
            tenant_id VARCHAR(32) NOT NULL,
            request_id VARCHAR(32) NOT NULL,
            approver VARCHAR(200) NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, request_id, approver)
        )
        """
    )
    op.execute("ALTER TABLE approval_votes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE approval_votes FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS approval_votes_tenant_isolation ON approval_votes")
    op.execute(
        "CREATE POLICY approval_votes_tenant_isolation ON approval_votes "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS approval_votes_tenant_isolation ON approval_votes")
    op.execute("DROP TABLE IF EXISTS approval_votes")
    op.execute("ALTER TABLE approval_requests DROP COLUMN IF EXISTS required_approvers")
