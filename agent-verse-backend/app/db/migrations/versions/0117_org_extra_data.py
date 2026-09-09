"""Add the ``extra_data`` JSONB column to every AI-Org-OS table.

The org ORM models (``app/org/models.py``) all declare an ``extra_data`` JSONB
column, but no migration ever added it to the tables — so every real query
against them (e.g. ``GET /v1/org``) failed with
``UndefinedColumnError: column organizations.extra_data does not exist`` and the
entire AI Organization feature returned HTTP 500 on the wired DB path. This adds
the column (idempotently) to all ten org tables.

Revision ID: 0117
Revises: 0116
"""

from __future__ import annotations

from alembic import op

revision = "0117"
down_revision = "0116"
branch_labels = None
depends_on = None

_ORG_TABLES = (
    "organizations",
    "org_departments",
    "org_teams",
    "org_roles",
    "org_capabilities",
    "org_missions",
    "org_workstreams",
    "org_tasks",
    "org_decisions",
    "org_blueprints",
)


def upgrade() -> None:
    for table in _ORG_TABLES:
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS extra_data JSONB NOT NULL DEFAULT '{{}}'"
        )


def downgrade() -> None:
    for table in _ORG_TABLES:
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS extra_data")
