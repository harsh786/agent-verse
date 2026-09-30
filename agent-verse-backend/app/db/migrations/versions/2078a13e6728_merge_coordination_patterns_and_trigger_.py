"""merge coordination patterns and trigger settings heads

Revision ID: 2078a13e6728
Revises: e5c8f2a9d1b4, e7a3c91d5b20
Create Date: 2026-10-01 04:22:11.259147
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "2078a13e6728"
down_revision: str | None = ("e5c8f2a9d1b4", "e7a3c91d5b20")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
