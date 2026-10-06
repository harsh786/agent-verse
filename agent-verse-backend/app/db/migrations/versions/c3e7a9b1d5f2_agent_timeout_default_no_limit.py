"""agents.timeout_seconds: 0 = no agent limit (the old 300 default was never used).

The agent timeout was stored with DEFAULT 300 and never read; now that goal
execution honours it (min(plan timeout, agent timeout)), every existing agent
would suddenly be capped at 5 minutes. Rows still holding the untouched default
become 0 ("no agent limit") and the column default becomes 0; explicitly
configured timeouts other than 300 are kept.

Revision ID: c3e7a9b1d5f2
Revises: b8d0f2a4c6e7
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c3e7a9b1d5f2"
down_revision: str | Sequence[str] | None = "b8d0f2a4c6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE agents ALTER COLUMN timeout_seconds SET DEFAULT 0")
    op.execute("UPDATE agents SET timeout_seconds = 0 WHERE timeout_seconds = 300")


def downgrade() -> None:
    op.execute("UPDATE agents SET timeout_seconds = 300 WHERE timeout_seconds = 0")
    op.execute("ALTER TABLE agents ALTER COLUMN timeout_seconds SET DEFAULT 300")
