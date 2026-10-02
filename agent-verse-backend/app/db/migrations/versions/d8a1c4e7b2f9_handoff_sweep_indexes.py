"""Partial indexes for the handoff sweeper (ORG-38).

The sweeper pages overdue ACCEPTED/EXECUTING handoffs by (deadline, id) and
recently released ones by updated_at across every tenant; both stay index scans
however many handoffs a deployment accumulates.

Revision ID: d8a1c4e7b2f9
Revises: c7fbea9807c1
"""

from __future__ import annotations

from alembic import op

revision = "d8a1c4e7b2f9"
down_revision = "c7fbea9807c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_handoffs_delegated_deadline "
        "ON handoffs (deadline, id) WHERE state IN ('accepted', 'executing')"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_handoffs_released_updated "
        "ON handoffs (updated_at, id) "
        "WHERE state IN ('completed', 'failed', 'cancelled', 'expired')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_handoffs_released_updated")
    op.execute("DROP INDEX IF EXISTS ix_handoffs_delegated_deadline")
