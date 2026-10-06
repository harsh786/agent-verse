"""Merge the B1 time-trigger branch (armed_at, trigger_delayed_fires) with the
chat-transcripts knowledge branch (CHAT-KB) into one head.

Both revise a12c4e6f8b10 independently and touch unrelated tables; no schema
change here.

Revision ID: f6b8d0e2a4c5
Revises: c3e8a1f5b7d2, e5a7c9b1d3f4
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "f6b8d0e2a4c5"
down_revision: tuple[str, ...] = ("c3e8a1f5b7d2", "e5a7c9b1d3f4")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
