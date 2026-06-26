"""Add agent_id to cost_ledger.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cost_ledger", sa.Column("agent_id", sa.String(64), nullable=True))
    op.create_index("ix_cost_ledger_agent_id", "cost_ledger", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_cost_ledger_agent_id", table_name="cost_ledger")
    op.drop_column("cost_ledger", "agent_id")
