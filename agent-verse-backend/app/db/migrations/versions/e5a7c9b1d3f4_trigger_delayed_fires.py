"""trigger_delayed_fires: event-relative relative_delay fires (B1-8)

A ``relative_delay`` trigger with an ``event_channel`` arms one delayed fire per
event on that channel: due ``offset`` seconds after the event (or after the
timestamp at ``relative_to_field`` in its payload). Each row is one pending
fire; the beat claims due rows with ``FOR UPDATE SKIP LOCKED`` through the
partial index on ``due_at`` (only unfired rows), so the claim stays an index
range scan however many fires are pending. The id is derived from (schedule,
event), so a redelivered event arms once. Rows go with their schedule
(ON DELETE CASCADE) and their tenant; tenant-isolated with FORCE RLS.

Revision ID: e5a7c9b1d3f4
Revises: d4f6b8a0c2e3
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5a7c9b1d3f4"
down_revision: str | None = "d4f6b8a0c2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS trigger_delayed_fires (
            id VARCHAR(64) PRIMARY KEY,
            tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            schedule_id VARCHAR(64) NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
            event_id VARCHAR(200) NOT NULL DEFAULT '',
            due_at TIMESTAMPTZ NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            fired_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_trigger_delayed_fires_due "
        "ON trigger_delayed_fires (due_at) WHERE fired_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_trigger_delayed_fires_fired "
        "ON trigger_delayed_fires (fired_at) WHERE fired_at IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_trigger_delayed_fires_schedule "
        "ON trigger_delayed_fires (tenant_id, schedule_id)"
    )
    op.execute("ALTER TABLE trigger_delayed_fires ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trigger_delayed_fires FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS trigger_delayed_fires_tenant_isolation ON trigger_delayed_fires"
    )
    op.execute(
        "CREATE POLICY trigger_delayed_fires_tenant_isolation ON trigger_delayed_fires "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS trigger_delayed_fires_tenant_isolation ON trigger_delayed_fires"
    )
    op.execute("DROP TABLE IF EXISTS trigger_delayed_fires")
