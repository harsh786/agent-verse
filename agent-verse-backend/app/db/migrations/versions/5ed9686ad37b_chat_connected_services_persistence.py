"""chat connected services persistence

Durable, tenant-isolated store for the chat "connected services" panel, which was
an in-memory dict that vanished on restart and diverged across pods
(distributed-scale audit X10). RLS enable+force+isolation policy like the other
tenant tables.

Revision ID: 5ed9686ad37b
Revises: 5bb611225952
Create Date: 2026-09-16 12:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "5ed9686ad37b"
down_revision: str | None = "5bb611225952"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_connected_services (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL,
            name          TEXT NOT NULL,
            url           TEXT NOT NULL DEFAULT '',
            scopes        JSONB NOT NULL DEFAULT '[]'::jsonb,
            status        TEXT NOT NULL DEFAULT 'pending',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            connected_at  TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_connected_services_tenant "
        "ON chat_connected_services (tenant_id)"
    )
    op.execute("ALTER TABLE chat_connected_services ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_connected_services FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'chat_connected_services'
                  AND policyname = 'chat_connected_services_isolation'
            ) THEN
                CREATE POLICY chat_connected_services_isolation ON chat_connected_services
                    USING (tenant_id = current_setting('app.tenant_id', true))
                    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS chat_connected_services_isolation ON chat_connected_services"
    )
    op.execute("DROP TABLE IF EXISTS chat_connected_services")
