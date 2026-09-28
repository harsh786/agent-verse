"""Persist trigger webhook signing secrets; give channel mappings a channel_id.

* ``schedules`` had no column for a trigger's webhook signing secret. The secret
  set on create / PATCH / rotate-secret lived only in the API process's memory,
  so after a restart (or on another replica) a signed trigger lost its secret
  and accepted UNSIGNED deliveries. The secret (and the previous one, honoured
  until ``webhook_secret_grace_until`` after a rotation) is now stored
  Fernet-encrypted via ``app.providers.vault`` — never in plaintext.
* ``channel_tenant_mappings`` had no ``channel_id`` column although every
  inbound channel lookup (``WHERE channel_type = :ct AND channel_id = :ci``) and
  the mapping insert used one, so the lookup always errored (→ tenant None) and
  no mapping could ever be created: inbound Slack/Teams/Discord/email/SMS/form
  messages never routed. The old unique index on ``(tenant_id, channel_type)``
  let a tenant map only ONE workspace per channel type and made
  ``ON CONFLICT DO NOTHING`` silently drop a second mapping; it is replaced by
  uniqueness of the external address itself, ``(channel_type, channel_id)`` —
  one external channel resolves to exactly one tenant. (The table was
  necessarily empty: no code path could insert into it.)

Revision ID: b3c4d5e6f7a9
Revises: e8f9a0b1c2d3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3c4d5e6f7a9"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schedules",
        sa.Column(
            "webhook_signature_secret_enc", sa.Text(), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "schedules",
        sa.Column(
            "webhook_signature_secret_prev_enc", sa.Text(), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "schedules",
        sa.Column("webhook_secret_grace_until", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(
        "channel_tenant_mappings",
        sa.Column("channel_id", sa.String(512), nullable=False, server_default=""),
    )
    op.execute("DROP INDEX IF EXISTS ix_channel_tenant_unique")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_channel_tenant_channel_unique "
        "ON channel_tenant_mappings (channel_type, channel_id) WHERE channel_id <> ''"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_channel_tenant_channel_unique")
    op.create_index(
        "ix_channel_tenant_unique",
        "channel_tenant_mappings",
        ["tenant_id", "channel_type"],
        unique=True,
    )
    op.drop_column("channel_tenant_mappings", "channel_id")
    op.drop_column("schedules", "webhook_secret_grace_until")
    op.drop_column("schedules", "webhook_signature_secret_prev_enc")
    op.drop_column("schedules", "webhook_signature_secret_enc")
