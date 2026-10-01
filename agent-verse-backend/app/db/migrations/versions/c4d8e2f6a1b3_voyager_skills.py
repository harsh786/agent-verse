"""voyager_skills: persistent, tenant-scoped Voyager skill library (VOYAGER-PERSIST)

Voyager publishes each skill it synthesises (a validated ``ProcedureContract``)
into a skill library. That library was a per-process dict, so skills vanished
on restart and were invisible to other replicas — which is why the strategy was
denied at StrategyRunner admission. A published skill version is immutable:
``UNIQUE (tenant_id, procedure_id, skill_version)``, and UPDATE is refused by a
trigger. FORCE RLS keyed on ``app_current_tenant_uuid()`` like the other
UUID-tenant tables.

Revision ID: c4d8e2f6a1b3
Revises: 8af71f807fdd
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c4d8e2f6a1b3"
down_revision: str | None = "8af71f807fdd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS voyager_skills (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      UUID NOT NULL,
            procedure_id   TEXT NOT NULL,
            skill_version  TEXT NOT NULL,
            contract       JSONB NOT NULL,
            steps          JSONB NOT NULL DEFAULT '[]'::jsonb,
            evidence_refs  JSONB NOT NULL DEFAULT '[]'::jsonb,
            goal_id        TEXT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_voyager_skills_version UNIQUE (tenant_id, procedure_id, skill_version)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_voyager_skills_tenant_created "
        "ON voyager_skills (tenant_id, created_at DESC)"
    )
    op.execute("ALTER TABLE voyager_skills ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE voyager_skills FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS voyager_skills_tenant_isolation ON voyager_skills")
    op.execute(
        "CREATE POLICY voyager_skills_tenant_isolation ON voyager_skills "
        "USING (tenant_id = app_current_tenant_uuid()) "
        "WITH CHECK (tenant_id = app_current_tenant_uuid())"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION voyager_skills_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'voyager skill versions are immutable';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER voyager_skills_no_update BEFORE UPDATE ON voyager_skills "
        "FOR EACH ROW EXECUTE FUNCTION voyager_skills_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS voyager_skills_no_update ON voyager_skills")
    op.execute("DROP FUNCTION IF EXISTS voyager_skills_immutable()")
    op.execute("DROP TABLE IF EXISTS voyager_skills")
