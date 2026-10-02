"""merge agent key and goal heartbeat heads

Revision ID: cf87de8eae52
Revises: 6b6e3e8586e8, a4e7c1b9d2f3
Create Date: 2026-10-02 09:57:24.894841
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "cf87de8eae52"
down_revision: str | None = ("6b6e3e8586e8", "a4e7c1b9d2f3")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
