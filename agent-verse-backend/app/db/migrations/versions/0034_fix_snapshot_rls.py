"""Fix agent_snapshots RLS policy key: app.current_tenant_id -> app.tenant_id

Revision ID: 0034
Revises: 0033
"""

from __future__ import annotations

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_snapshots_tenant_isolation ON agent_snapshots")
    op.execute(
        "CREATE POLICY agent_snapshots_tenant_isolation ON agent_snapshots "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_snapshots_tenant_isolation ON agent_snapshots")
    op.execute(
        "CREATE POLICY agent_snapshots_tenant_isolation ON agent_snapshots "
        "USING (tenant_id = current_setting('app.current_tenant_id', TRUE))"
    )
