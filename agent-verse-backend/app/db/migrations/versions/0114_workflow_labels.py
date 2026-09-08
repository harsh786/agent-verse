"""0114 — add labels column to workflows.

``WorkflowResponse.labels`` (dict[str, str]) is a required API field and
``WorkflowService.list`` filters by label, but the ``workflows`` table had no
labels column, so create round-tripped nothing and the response failed
validation (HTTP 500). This adds a JSONB ``labels`` column defaulting to an
empty object, matching the ORM model.

Revision ID: 0114
Revises: 0113
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0114"
down_revision = "0113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("workflows", "labels")
