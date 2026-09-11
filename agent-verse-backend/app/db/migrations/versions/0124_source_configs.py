"""source_configs — fill gaps so ingestion Sources fully persist (item 6).

The table itself exists since 0109/0110 (PK ``id``), but three SourceConfig
fields had no column, so a Source created through the durable store could not
round-trip: ``cursor_field`` (incremental cursor column name), ``allowed_user_ids``
(ACL), and ``near_dup_threshold`` (semantic dedup). This adds them, plus a
partial index for the beat due-scan. Additive and idempotent — it does NOT
recreate the table or its RLS (0109/0110 own those).

Revision ID: 0124
Revises: 0123
"""

from __future__ import annotations

from alembic import op

revision = "0124"
down_revision = "0123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE source_configs "
        "ADD COLUMN IF NOT EXISTS cursor_field TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE source_configs "
        "ADD COLUMN IF NOT EXISTS allowed_user_ids JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE source_configs "
        "ADD COLUMN IF NOT EXISTS near_dup_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.0"
    )
    # Partial index for the beat due-scan (enabled, non-streaming sources).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_source_configs_due "
        "ON source_configs (last_synced_at) "
        "WHERE enabled IS TRUE AND sync_mode <> 'streaming'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_source_configs_due")
    op.execute("ALTER TABLE source_configs DROP COLUMN IF EXISTS near_dup_threshold")
    op.execute("ALTER TABLE source_configs DROP COLUMN IF EXISTS allowed_user_ids")
    op.execute("ALTER TABLE source_configs DROP COLUMN IF EXISTS cursor_field")
