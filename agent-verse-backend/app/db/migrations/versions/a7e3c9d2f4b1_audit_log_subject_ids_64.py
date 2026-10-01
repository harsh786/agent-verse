"""audit_log.goal_id / step_id: VARCHAR(32) -> VARCHAR(64) (WF-AUDIT)

``goal_id`` is the audited subject. Goal ids are 32-char hex, but workflow ids
(and runs) are dashed UUIDs (36 chars) and workflow step ids are free-form, so
every workflow audit row (``workflow.created``, ``workflow.access_denied``, ...)
failed the INSERT with "value too long for type character varying(32)" — and
since audit persistence is fire-and-forget, silently. Widening a VARCHAR is a
catalog-only change in Postgres (no table rewrite, no row trigger fires).

Revision ID: a7e3c9d2f4b1
Revises: e5c8f2a9d1b4
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7e3c9d2f4b1"
down_revision: str | None = "e5c8f2a9d1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "audit_log", "goal_id", type_=sa.String(64), existing_type=sa.String(32),
        existing_nullable=False,
    )
    op.alter_column(
        "audit_log", "step_id", type_=sa.String(64), existing_type=sa.String(32),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "audit_log", "step_id", type_=sa.String(32), existing_type=sa.String(64),
        existing_nullable=True, postgresql_using="left(step_id, 32)",
    )
    op.alter_column(
        "audit_log", "goal_id", type_=sa.String(32), existing_type=sa.String(64),
        existing_nullable=False, postgresql_using="left(goal_id, 32)",
    )
