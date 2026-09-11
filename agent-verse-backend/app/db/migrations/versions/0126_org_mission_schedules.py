"""org_mission_schedules — cron schedules that autonomously launch org missions.

Phase 1 of "scheduled autonomous mission → publish": a recurring/one-off cron
that the fire_due_org_mission_schedules beat task turns into an org mission,
dispatched through the crash-safe execute_org_mission worker. Tenant-isolated
via RLS like the other org_* tables.

Revision ID: 0126
Revises: 0125
"""

from __future__ import annotations

from alembic import op

revision = "0126"
down_revision = "0125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS org_mission_schedules (
            id                UUID PRIMARY KEY,
            tenant_id         UUID NOT NULL,
            org_id            UUID NOT NULL REFERENCES organizations(id),
            name              VARCHAR(200) NOT NULL DEFAULT '',
            title             VARCHAR(500) NOT NULL,
            objective         TEXT DEFAULT '',
            priority          VARCHAR(20) NOT NULL DEFAULT 'medium',
            autonomy_level    INTEGER,
            dept_id           UUID,
            cron_expression   VARCHAR(120) NOT NULL DEFAULT '',
            timezone          VARCHAR(64) NOT NULL DEFAULT 'UTC',
            enabled           BOOLEAN NOT NULL DEFAULT TRUE,
            next_fire_at      TIMESTAMPTZ,
            last_fired_at     TIMESTAMPTZ,
            last_mission_id   UUID,
            fire_count        INTEGER NOT NULL DEFAULT 0,
            publish_config    JSONB,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_org_sched_tenant_org "
        "ON org_mission_schedules (tenant_id, org_id)"
    )
    # Beat due-scan: enabled schedules ordered by next_fire_at.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_org_sched_due "
        "ON org_mission_schedules (enabled, next_fire_at)"
    )
    # Row-level tenant isolation, matching the other org_* tables.
    op.execute("ALTER TABLE org_mission_schedules ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_mission_schedules FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'org_mission_schedules'
                  AND policyname = 'org_mission_schedules_isolation'
            ) THEN
                CREATE POLICY org_mission_schedules_isolation ON org_mission_schedules
                    USING (tenant_id::text = current_setting('app.tenant_id', TRUE));
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS org_mission_schedules CASCADE")
