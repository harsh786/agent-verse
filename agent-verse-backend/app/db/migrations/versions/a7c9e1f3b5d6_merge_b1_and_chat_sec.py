"""Merge the B1 time-trigger head (f6b8d0e2a4c5) with main's CHAT-SEC head
(e9a3c5d7f1b2) into one head. No schema change.

Revision ID: a7c9e1f3b5d6
Revises: e9a3c5d7f1b2, f6b8d0e2a4c5
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "a7c9e1f3b5d6"
down_revision: tuple[str, ...] = ("e9a3c5d7f1b2", "f6b8d0e2a4c5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
