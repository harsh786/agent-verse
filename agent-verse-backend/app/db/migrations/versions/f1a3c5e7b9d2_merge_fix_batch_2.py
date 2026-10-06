"""Merge the fix-batch-2 heads into one: agent timeout default (c3e7a9b1d5f2),
chat/knowledge fixes (d4e6f8a0b2c3: FTS code tokens; source-less DLQ) and the
B2 webhook fixes (e5f7a9b1c3d5: DLQ idempotency keys, vendor replay guard).
No schema change.

Revision ID: f1a3c5e7b9d2
Revises: c3e7a9b1d5f2, d4e6f8a0b2c3, e5f7a9b1c3d5
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "f1a3c5e7b9d2"
down_revision: tuple[str, ...] = ("c3e7a9b1d5f2", "d4e6f8a0b2c3", "e5f7a9b1c3d5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
