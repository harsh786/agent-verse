"""GIN index on workflows.definition for the schedule beat's containment scan.

``fire_due_workflow_schedules`` runs every 60 seconds on every beat replica and
used to read **every published workflow of every tenant, with its full JSON
definition**, into one Python list, then test each one for a schedule trigger in
Python. At ten thousand tenants with fifty published workflows each that is half
a million JSONB documents a minute to find the handful actually due.

The scan now filters server-side with two JSONB containment predicates (the
visual builder's plural ``triggers`` list and the DSL's singular ``trigger``
object — see ``app.workflow.trigger_extract``), in bounded keyset pages. A GIN
index on ``definition`` is what makes those containment tests an index scan
rather than the same full read with extra steps.

``jsonb_ops`` (the default) rather than ``jsonb_path_ops`` so the index also
serves existence/key lookups on the same column, which the builder and template
paths use.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

from __future__ import annotations

from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflows_definition_gin "
        "ON workflows USING gin (definition)"
    )
    # The scan's driving filter is status='published'; pairing it with the id
    # keyset order keeps the paged read cheap as the fleet grows.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflows_published_id "
        "ON workflows (status, id) WHERE status = 'published'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_workflows_published_id")
    op.execute("DROP INDEX IF EXISTS idx_workflows_definition_gin")
