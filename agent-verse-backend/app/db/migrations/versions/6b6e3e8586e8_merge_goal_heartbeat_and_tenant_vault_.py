"""merge goal heartbeat and tenant vault heads

Revision ID: 6b6e3e8586e8
Revises: d4e0f2a8b3c9, e4f7a2c9d1b8
Create Date: 2026-10-02 02:27:44.567113
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "6b6e3e8586e8"
down_revision: str | None = ("d4e0f2a8b3c9", "e4f7a2c9d1b8")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
