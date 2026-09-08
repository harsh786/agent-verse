"""0115 — backfill workflow_definitions from the legacy workflows table.

The visual-builder create path (``POST /api/v1/workflows`` → ``_WorkflowStore``)
persists to the legacy ``workflows`` table (Text id, 0046). The run engine
(``WorkflowRunner`` + ``PostgresWorkflowRunStore``) is built around
``workflow_definitions`` (uuid id, 0108), and ``workflow_runs.workflow_id``
FK-references it. Nothing populated ``workflow_definitions``, so triggering an
API-created workflow failed a ``workflow_runs_workflow_id_fkey`` violation.

Going forward the store mirrors every create/update/delete into
``workflow_definitions`` (see ``_WorkflowStore._bridge_upsert_definition``). This
migration reconciles the *existing* rows: it copies every legacy ``workflows``
row into ``workflow_definitions`` (same id) so previously-created workflows
become triggerable too.

Cross-tenant backfill under FORCE RLS: RLS is briefly disabled (this migration's
role owns both tables), the copy runs, then RLS + FORCE are restored exactly as
0046/0108 configured them. Only rows whose id and tenant_id are valid UUIDs are
copied — the run engine casts both to uuid, so non-UUID tenants could not use it
anyway.

Revision ID: 0115
Revises: 0114
Create Date: 2026-09-08
"""

from __future__ import annotations

from alembic import op

revision = "0115"
down_revision = "0114"
branch_labels = None
depends_on = None

_UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def upgrade() -> None:
    # Temporarily lift RLS so the copy spans all tenants in one statement.
    op.execute("ALTER TABLE workflows DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_definitions DISABLE ROW LEVEL SECURITY")

    op.execute(
        f"""
        INSERT INTO workflow_definitions
            (id, tenant_id, name, slug, description, definition_json, status, version)
        SELECT
            w.id::uuid,
            w.tenant_id::uuid,
            w.name,
            w.id AS slug,           -- id is globally unique → unique per tenant
            COALESCE(w.description, ''),
            w.definition,
            w.status,
            '1.0.0'
        FROM workflows w
        WHERE w.id ~ '{_UUID_RE}'
          AND w.tenant_id ~ '{_UUID_RE}'
          AND NOT EXISTS (
              SELECT 1 FROM workflow_definitions d WHERE d.id = w.id::uuid
          )
        ON CONFLICT (id) DO NOTHING
        """
    )

    # Restore RLS exactly as 0046 (workflows) and 0108 (workflow_definitions) set it.
    op.execute("ALTER TABLE workflows ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflows FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_definitions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_definitions FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Data backfill: bridged rows are indistinguishable from any natively-created
    # workflow_definitions row, so removing them on downgrade could delete
    # legitimate data. Intentionally a no-op.
    pass
