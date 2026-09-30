"""merge fix-wave-8 heads (workflows, scheduling)

Revision ID: 795ba979e293
Revises: d3a7c1e9f2b4, e2b4d6f8a0c1
Create Date: 2026-09-30 21:42:56.945774
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = '795ba979e293'
down_revision: str | None = ('d3a7c1e9f2b4', 'e2b4d6f8a0c1')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
