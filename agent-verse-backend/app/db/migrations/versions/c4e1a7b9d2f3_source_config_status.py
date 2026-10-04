"""source_configs.config_status — park Sources that cannot index (L-02).

A Source with no target collection failed every document into the DLQ on every
scheduled sync, forever ("source <id> has no collection_id; nothing can be
indexed"). ``config_status`` = ``needs_configuration`` (+ ``config_status_reason``)
parks it: the beat due-scan and the DLQ retry skip it and the API shows why,
until an update sets a collection. Existing collection-less rows are parked here,
so they stop being dispatched at deploy time. The due-scan's partial index gains
the ``config_status = 'ok'`` predicate.

Revision ID: c4e1a7b9d2f3
Revises: f1e790b4050a
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c4e1a7b9d2f3"
down_revision: str | None = "f1e790b4050a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REASON = (
    "no target knowledge collection (collection_id) is set; "
    "choose a collection for this source to resume syncing"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE source_configs "
        "ADD COLUMN IF NOT EXISTS config_status VARCHAR(32) NOT NULL DEFAULT 'ok'"
    )
    op.execute(
        "ALTER TABLE source_configs "
        "ADD COLUMN IF NOT EXISTS config_status_reason TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "UPDATE source_configs SET config_status = 'needs_configuration', "
        f"config_status_reason = '{_REASON}' "
        "WHERE COALESCE(btrim(collection_id), '') = '' AND config_status = 'ok'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_source_configs_due_ok "
        "ON source_configs (last_synced_at) "
        "WHERE enabled IS TRUE AND sync_mode <> 'streaming' AND config_status = 'ok'"
    )
    op.execute("DROP INDEX IF EXISTS ix_source_configs_due")


def downgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_source_configs_due "
        "ON source_configs (last_synced_at) "
        "WHERE enabled IS TRUE AND sync_mode <> 'streaming'"
    )
    op.execute("DROP INDEX IF EXISTS ix_source_configs_due_ok")
    op.execute("ALTER TABLE source_configs DROP COLUMN IF EXISTS config_status_reason")
    op.execute("ALTER TABLE source_configs DROP COLUMN IF EXISTS config_status")
