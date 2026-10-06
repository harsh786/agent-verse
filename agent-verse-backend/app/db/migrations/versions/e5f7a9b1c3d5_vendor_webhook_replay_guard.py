"""vendor_webhook_replay_guard: body-signed vendor deliveries replayable after purge (DEF-NEW-3).

GitHub (and Confluence, Linear, Sentry, PagerDuty, Salesforce, Grafana, and a
Jira body without a ``timestamp``) sign only the body, so a delivery is
deduplicated on its signed body through ``trigger_events`` — which the retention
job purges after ``DATA_RETENTION_DAYS``; a captured delivery replayed after that
fired again. This table keeps the signed-body key of every such delivery that
ran (no payload) while its trigger exists and the secret that signed it is still
accepted (see ``app/triggers/webhooks/replay.py``). Tenant-scoped, FORCE RLS.

Revision ID: e5f7a9b1c3d5
Revises: d4e6f8a0b2c4
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5f7a9b1c3d5"
down_revision: str | Sequence[str] | None = "d4e6f8a0b2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "vendor_webhook_replay_guard"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            tenant_id      TEXT NOT NULL,
            trigger_id     TEXT NOT NULL,
            signed_key     TEXT NOT NULL,
            first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, trigger_id, signed_key)
        )
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.execute(
        f"CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
