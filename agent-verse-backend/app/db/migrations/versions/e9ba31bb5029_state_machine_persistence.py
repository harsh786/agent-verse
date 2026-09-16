"""state machine persistence

Durable backing for the state-machine registry, which was in-memory only
(definitions and instances lost on restart, not multi-worker safe) while the
REST API implied durability. These tables persist definitions (states +
transitions as JSONB) and their running instances (history as JSONB).
Tenant-isolated via RLS like the other tenant tables; tenant_id is TEXT to
match TenantContext.tenant_id.

Revision ID: e9ba31bb5029
Revises: 6c850a1c4c09
Create Date: 2026-09-16 10:14:47.936517
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e9ba31bb5029"
down_revision: str | None = "6c850a1c4c09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("trigger_state_machine_definitions", "trigger_state_machine_instances")


def _force_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = '{table}' AND policyname = '{table}_isolation'
            ) THEN
                CREATE POLICY {table}_isolation ON {table}
                    USING (tenant_id = current_setting('app.tenant_id', true))
                    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS trigger_state_machine_definitions (
            machine_id  TEXT NOT NULL,
            tenant_id   TEXT NOT NULL,
            name        TEXT NOT NULL DEFAULT '',
            definition  JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (machine_id, tenant_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tsm_definitions_tenant "
        "ON trigger_state_machine_definitions (tenant_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS trigger_state_machine_instances (
            instance_id    TEXT PRIMARY KEY,
            machine_id     TEXT NOT NULL,
            tenant_id      TEXT NOT NULL,
            entity_id      TEXT NOT NULL,
            current_state  TEXT NOT NULL,
            history        JSONB NOT NULL DEFAULT '[]'::jsonb,
            status         TEXT NOT NULL DEFAULT 'running',
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tsm_inst_tenant_entity "
        "ON trigger_state_machine_instances (tenant_id, entity_id)"
    )
    for table in _TABLES:
        _force_rls(table)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
    op.execute("DROP TABLE IF EXISTS trigger_state_machine_instances")
    op.execute("DROP TABLE IF EXISTS trigger_state_machine_definitions")
