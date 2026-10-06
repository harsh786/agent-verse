"""Merge the B1 time-trigger head (a7c9e1f3b5d6) with main's chat-durability
head (a6c2e8f4b0d3) into one head. No schema change.

Revision ID: b8d0f2a4c6e7
Revises: a6c2e8f4b0d3, a7c9e1f3b5d6
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "b8d0f2a4c6e7"
down_revision: tuple[str, ...] = ("a6c2e8f4b0d3", "a7c9e1f3b5d6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
