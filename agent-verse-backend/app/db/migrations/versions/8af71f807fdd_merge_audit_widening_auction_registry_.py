"""merge audit widening, auction registry and memory heads

Revision ID: 8af71f807fdd
Revises: a7e3c9d2f4b1, b4d81c6e2a90, d94f6b8c0e53
Create Date: 2026-10-01 10:54:24.845756
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "8af71f807fdd"
down_revision: str | None = ("a7e3c9d2f4b1", "b4d81c6e2a90", "d94f6b8c0e53")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
