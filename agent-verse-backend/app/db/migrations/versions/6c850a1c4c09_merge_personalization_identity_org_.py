"""merge personalization_identity + org_brain_guardrail_trace

Revision ID: 6c850a1c4c09
Revises: 0131, 0132_org_brain_guardrail_trace
Create Date: 2026-09-15 19:57:33.642700
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = '6c850a1c4c09'
down_revision: str | None = ('0131', '0132_org_brain_guardrail_trace')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
