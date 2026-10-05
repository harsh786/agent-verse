"""merge heads (stabilize main 2026-10-05)

Joins the heads left by the stabilization picks: MEM-42 (b42d6e1f8a37),
MEM-53 (b53e9d1f7a24), HEALTH-06 (b7d3e1f0a9c2) and main (c087b9d58f6f).

Revision ID: e2e2811317df
Revises: b42d6e1f8a37, b53e9d1f7a24, b7d3e1f0a9c2, c087b9d58f6f
Create Date: 2026-10-05 10:33:54.971210
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "e2e2811317df"
down_revision: str | tuple[str, ...] | None = (
    "b42d6e1f8a37",
    "b53e9d1f7a24",
    "b7d3e1f0a9c2",
    "c087b9d58f6f",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
