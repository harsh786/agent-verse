"""merge memory and coordination heads

Revision ID: 4075a45ac5a5
Revises: 2078a13e6728, b72d4f6a8c31
Create Date: 2026-10-01 09:12:55.369285
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "4075a45ac5a5"
down_revision: str | None = ("2078a13e6728", "b72d4f6a8c31")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
