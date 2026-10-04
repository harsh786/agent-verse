"""merge alembic heads (branch merge)

Revision ID: f1e790b4050a
Revises: 5ea3dc985432, d46b0c2e4f85, d8a1c4e7b2f9, e7a1c4d2b9f2
Create Date: 2026-10-05 00:14:10.516423
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "f1e790b4050a"
down_revision: str | None = ("5ea3dc985432", "d46b0c2e4f85", "d8a1c4e7b2f9", "e7a1c4d2b9f2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
