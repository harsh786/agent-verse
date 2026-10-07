"""Merge the batch-4 heads into one: the batch-3 head (b3d5f7a9c1e3),
routing_runtime table drop (b3e7d1f9a5c2), tenant email settings (c4e6a8b0d2f4),
fan-out child deadlines (e4c8a2f6d9b1),
orphaned experiment table drop (e5f1a9c3d7b2) and golden dataset table drop
(f7d9b1c3e5a8), and the agent autonomy re-validation state (c3e5a7b9d1f4) and proactive outreach
preferences (d6f8b0c2e4a7).
No schema change.

Revision ID: a9c1e3f5b7d4
Revises: b3d5f7a9c1e3, b3e7d1f9a5c2, c4e6a8b0d2f4, e4c8a2f6d9b1, e5f1a9c3d7b2, f7d9b1c3e5a8,
         c3e5a7b9d1f4, d6f8b0c2e4a7
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "a9c1e3f5b7d4"
down_revision: tuple[str, ...] = (
    "b3d5f7a9c1e3",
    "b3e7d1f9a5c2",
    "c4e6a8b0d2f4",
    "e4c8a2f6d9b1",
    "e5f1a9c3d7b2",
    "f7d9b1c3e5a8",
    "c3e5a7b9d1f4",
    "d6f8b0c2e4a7",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
