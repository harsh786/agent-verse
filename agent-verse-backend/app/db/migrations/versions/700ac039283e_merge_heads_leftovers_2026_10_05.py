"""merge heads (leftovers 2026-10-05)

Revision ID: 700ac039283e
Revises: a7c3e9f1b2d4, b8d5f0e3c2a4, c1e5a7b9d3f2, e3d7f9b1c5a2
Create Date: 2026-10-05 12:30:43.190117
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "700ac039283e"
down_revision: str | None = ("a7c3e9f1b2d4", "b8d5f0e3c2a4", "c1e5a7b9d3f2", "e3d7f9b1c5a2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
