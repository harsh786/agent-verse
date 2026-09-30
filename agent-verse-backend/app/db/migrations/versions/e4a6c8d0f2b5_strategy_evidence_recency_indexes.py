"""strategy_certification_evidence: recency + expiry indexes.

The tenant catalogue read ranks the newest rows per strategy
(``tenant_id, strategy_id ORDER BY observed_at DESC``) and the new purge task
deletes by ``expires_at``; the only index was
(tenant_id, strategy_id, adapter_version), which serves neither (CORE-17).

Revision ID: e4a6c8d0f2b5
Revises: d3f5a7b9c1e4
"""

from __future__ import annotations

from alembic import op

revision = "e4a6c8d0f2b5"
down_revision = "d3f5a7b9c1e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_strategy_evidence_recent "
        "ON strategy_certification_evidence (tenant_id, strategy_id, observed_at DESC) "
        "INCLUDE (expires_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_strategy_evidence_expires_at "
        "ON strategy_certification_evidence (expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_strategy_evidence_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_strategy_evidence_recent")
