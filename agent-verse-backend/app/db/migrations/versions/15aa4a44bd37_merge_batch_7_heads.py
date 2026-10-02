"""merge batch 7 heads

Revision ID: 15aa4a44bd37
Revises: b9d4f2a6c8e1, e2c6d8a0b4f1, e5f1a3b9c4d0, e7a1c4d2b9f1
Create Date: 2026-10-02 23:33:22.683465
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "15aa4a44bd37"
down_revision: str | None = ("b9d4f2a6c8e1", "e2c6d8a0b4f1", "e5f1a3b9c4d0", "e7a1c4d2b9f1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
