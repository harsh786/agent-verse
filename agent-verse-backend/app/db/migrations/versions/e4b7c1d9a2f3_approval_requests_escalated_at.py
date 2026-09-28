"""approval_requests.escalated_at: HITL SLA enforcement acts on the real table.

``hitl_approval_requests`` (0056) was never written by application code, so
``/governance/approvals/sla-stats`` always answered zeros and the
``enforce_hitl_sla`` beat task was a no-op. SLA stats and enforcement now run on
``approval_requests`` (the table the HITL gateway actually writes), joined to the
tenant's ``approval_sla_configs``. ``escalated_at`` records that a pending
request breached its response SLA so it is escalated exactly once.

Revision ID: e4b7c1d9a2f3
Revises: e8f9a0b1c2d3
"""

from __future__ import annotations

from alembic import op

revision = "e4b7c1d9a2f3"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMPTZ")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_approval_requests_pending_sla "
        "ON approval_requests (tenant_id, risk_level, created_at) "
        "WHERE status = 'pending'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_approval_requests_pending_sla")
    op.execute("ALTER TABLE approval_requests DROP COLUMN IF EXISTS escalated_at")
