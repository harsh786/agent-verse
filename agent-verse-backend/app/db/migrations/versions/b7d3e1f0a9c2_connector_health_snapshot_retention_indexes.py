"""connector_health_snapshots retention + history indexes (HEALTH-06).

The health sweep writes one row per connector per run and nothing pruned the
table. ``prune_connector_health_snapshots`` deletes by ``checked_at`` (needs an
index on it), and the history endpoint reads one connector's latest rows
(``tenant_id, server_id, checked_at DESC``).

Revision ID: b7d3e1f0a9c2
Revises: f1e790b4050a
"""

from __future__ import annotations

from alembic import op

revision = "b7d3e1f0a9c2"
down_revision = "f1e790b4050a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ch_snapshots_checked_at "
        "ON connector_health_snapshots (checked_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ch_snapshots_tenant_server_checked "
        "ON connector_health_snapshots (tenant_id, server_id, checked_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ch_snapshots_tenant_server_checked")
    op.execute("DROP INDEX IF EXISTS ix_ch_snapshots_checked_at")
