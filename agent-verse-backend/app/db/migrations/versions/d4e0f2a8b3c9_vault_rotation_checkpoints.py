"""Checkpoints for the resumable vault master-key rotation (``agentverse vault-rotate``).

One row per rotation (keyed by the new key's fingerprint): the last committed
position (store, tenant, last primary key), the running report and the status.
Platform-global system data: FORCE RLS with no policy, so only the maintenance
role (BYPASSRLS, ``system_session``) can read or write it.

Revision ID: d4e0f2a8b3c9
Revises: c3d9e1f7a2b8
"""

from __future__ import annotations

from alembic import op

revision = "d4e0f2a8b3c9"
down_revision = "c3d9e1f7a2b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vault_rotation_checkpoints (
            rotation_id  VARCHAR(64) PRIMARY KEY,
            position     JSONB NOT NULL DEFAULT '{}'::jsonb,
            report       JSONB NOT NULL DEFAULT '{}'::jsonb,
            status       VARCHAR(20) NOT NULL DEFAULT 'running',
            started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("ALTER TABLE vault_rotation_checkpoints ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE vault_rotation_checkpoints FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vault_rotation_checkpoints")
