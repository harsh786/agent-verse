"""merge batch 8 heads

Revision ID: a4d51be05bf1
Revises: b5d1e3f7a9c2, c45f9a1b3e74, e5a1c7d93b20
Create Date: 2026-10-03 06:47:55.777939
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "a4d51be05bf1"
down_revision: str | None = ("b5d1e3f7a9c2", "c45f9a1b3e74", "e5a1c7d93b20")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
