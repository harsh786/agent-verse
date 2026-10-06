"""workflow_webhook_replay_guard: signed workflow webhook deliveries replayable (WF-REPLAY-1).

``POST /wf-hooks/{token}`` with ``auth: hmac`` deduplicated only on an unsigned
delivery-id header (and not at all without one), so a captured signed delivery
could be replayed indefinitely. This table keeps the signed key of every
delivery that was accepted (no payload), with a fingerprint of the secret that
signed it, while the workflow exists (see ``app/workflow/webhook_replay.py``).
It is separate from ``vendor_webhook_replay_guard``, whose purge treats a row
with no ``schedules`` row as dead. Tenant-scoped, FORCE RLS.

Revision ID: a7c9e1f3b5d7
Revises: f1a3c5e7b9d2
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a7c9e1f3b5d7"
down_revision: str | Sequence[str] | None = "f1a3c5e7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workflow_webhook_replay_guard"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            tenant_id      TEXT NOT NULL,
            workflow_id    TEXT NOT NULL,
            signed_key     TEXT NOT NULL,
            secret_ref     TEXT NOT NULL,
            first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, workflow_id, signed_key)
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
