"""merge fix-wave-8 heads (agent core)

Revision ID: 61e5532b6fb3
Revises: 795ba979e293, e4a6c8d0f2b5
Create Date: 2026-09-30 21:49:43.373650
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = '61e5532b6fb3'
down_revision: str | None = ('795ba979e293', 'e4a6c8d0f2b5')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
