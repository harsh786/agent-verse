"""merge heads decisions byok

Revision ID: ec9ee37e6779
Revises: 1d3a2404e7be, b7e2c4f9a1d6
Create Date: 2026-10-05 20:33:50.226061
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "ec9ee37e6779"
down_revision: str | None = ("1d3a2404e7be", "b7e2c4f9a1d6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
