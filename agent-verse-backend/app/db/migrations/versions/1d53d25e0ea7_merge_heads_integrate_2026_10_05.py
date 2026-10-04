"""merge heads (integrate 2026-10-05)

Revision ID: 1d53d25e0ea7
Revises: a47e3c9d1b52, c4e1a7b9d2f3, c8e41a2d9f37, d4f8b0e2a6c3
Create Date: 2026-10-05 00:51:20.246244
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "1d53d25e0ea7"
down_revision: str | None = ("a47e3c9d1b52", "c4e1a7b9d2f3", "c8e41a2d9f37", "d4f8b0e2a6c3")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
