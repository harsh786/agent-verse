"""department memory persistence

Durable, tenant-isolated store for department-scoped knowledge, which lived in an
in-memory DepartmentMemory dict — entries vanished on restart and diverged across
pods (distributed-scale audit X17). RLS enable+force+isolation.

Revision ID: 9f4636d0feaf
Revises: 44a3062580a1
Create Date: 2026-09-16 13:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "9f4636d0feaf"
down_revision: str | None = "44a3062580a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS department_memory_entries (
            entry_id    TEXT PRIMARY KEY,
            dept_id     TEXT NOT NULL,
            org_id      TEXT NOT NULL,
            tenant_id   TEXT NOT NULL,
            content     TEXT NOT NULL,
            source      TEXT NOT NULL DEFAULT '',
            confidence  DOUBLE PRECISION NOT NULL DEFAULT 0.9,
            tags        JSONB NOT NULL DEFAULT '[]'::jsonb,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            corrections JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_department_memory_dept "
        "ON department_memory_entries (tenant_id, dept_id)"
    )
    op.execute("ALTER TABLE department_memory_entries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE department_memory_entries FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'department_memory_entries'
                  AND policyname = 'department_memory_entries_isolation'
            ) THEN
                CREATE POLICY department_memory_entries_isolation ON department_memory_entries
                    USING (tenant_id = current_setting('app.tenant_id', true))
                    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS department_memory_entries_isolation "
        "ON department_memory_entries"
    )
    op.execute("DROP TABLE IF EXISTS department_memory_entries")
