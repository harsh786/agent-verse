"""merge alembic heads (merge everything 2026-10-05)

Revision ID: c087b9d58f6f
Revises: 1d53d25e0ea7, a08f177a0001
Create Date: 2026-10-05 01:29:55.487205
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "c087b9d58f6f"
down_revision: str | None = ("1d53d25e0ea7", "a08f177a0001")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
