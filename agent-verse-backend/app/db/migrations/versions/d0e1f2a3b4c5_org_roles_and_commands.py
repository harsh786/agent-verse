"""org_custom_roles + org_commands: persist what lived in per-process dicts.

The org router kept custom roles and Universal Command Gateway history in
module-level dicts keyed by org id alone ("swapped for DB in lifespan" — it
never was). With several API replicas a role created on one did not exist on
the others, a command submitted on one could not be polled on another, and
every restart erased both. Written/read by ``app/org/runtime_store.py``.

Both tables are tenant-isolated by FORCE'd RLS using ``app_current_tenant_uuid()``
(``c9d0e1f2a3b4``), which matches dashless tenant ids and never raises.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""

from __future__ import annotations

from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None

_TABLES = ("org_custom_roles", "org_commands")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS org_custom_roles (
            id           UUID PRIMARY KEY,
            tenant_id    UUID NOT NULL,
            org_id       UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name         VARCHAR(200) NOT NULL,
            description  TEXT NOT NULL DEFAULT '',
            permissions  JSONB NOT NULL DEFAULT '[]',
            member_count INTEGER NOT NULL DEFAULT 0,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_custom_roles_tenant_org "
        "ON org_custom_roles (tenant_id, org_id, created_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS org_commands (
            command_id      UUID PRIMARY KEY,
            tenant_id       UUID NOT NULL,
            org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            command         TEXT NOT NULL,
            channel         VARCHAR(64) NOT NULL,
            status          VARCHAR(32) NOT NULL,
            requires_2fa    BOOLEAN NOT NULL DEFAULT false,
            conversation_id VARCHAR(200),
            goal_id         VARCHAR(64),
            error           TEXT,
            result          JSONB,
            submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_commands_tenant_org_submitted "
        "ON org_commands (tenant_id, org_id, submitted_at)"
    )
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_isolation ON {table} "
            "USING (tenant_id = app_current_tenant_uuid()) "
            "WITH CHECK (tenant_id = app_current_tenant_uuid())"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
