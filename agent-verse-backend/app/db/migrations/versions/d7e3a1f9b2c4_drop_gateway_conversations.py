"""Drop the unused gateway_conversations table (DROP-GATEWAY-CONVERSATIONS).

``gateway_conversations`` (0112) was read and written only by
``app/gateway/conversation.py:ConversationManager``, which never had a caller
and was deleted; channel conversations persist in the durable chat channel
sessions (``chat_channel_sessions`` / ``chat_principal_sessions``, e5c1a9d3b7f2).
The table held 0 rows on the dev database.

Downgrade recreates it as it stood at the previous head: columns and indexes
from 0112, RLS enabled and FORCE'd, and the ``tenant_isolation`` policy in the
``app_current_tenant_uuid()`` form c9d0e1f2a3b4 rewrote it to.

Revision ID: d7e3a1f9b2c4
Revises: c4d8e2f6a1b3
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d7e3a1f9b2c4"
down_revision: str | None = "c4d8e2f6a1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gateway_conversations")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gateway_conversations (
            id               UUID PRIMARY KEY,
            tenant_id        UUID NOT NULL,
            org_id           UUID REFERENCES organizations(id) ON DELETE CASCADE,
            channel          VARCHAR(50) NOT NULL,
            channel_user_id  VARCHAR(500),
            conversation_key VARCHAR(500),
            turns            JSONB DEFAULT '[]',
            context_summary  TEXT,
            last_command_at  TIMESTAMPTZ,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_gw_conv_tenant ON gateway_conversations(tenant_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gw_conv_org ON gateway_conversations(tenant_id, org_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gw_conv_channel "
        "ON gateway_conversations(tenant_id, channel)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_gw_conv_key "
        "ON gateway_conversations(tenant_id, channel, channel_user_id, conversation_key) "
        "WHERE conversation_key IS NOT NULL AND channel_user_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gw_conv_last_cmd "
        "ON gateway_conversations(tenant_id, last_command_at DESC)"
    )
    op.execute("ALTER TABLE gateway_conversations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE gateway_conversations FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON gateway_conversations")
    op.execute(
        "CREATE POLICY tenant_isolation ON gateway_conversations AS PERMISSIVE FOR ALL "
        "USING (tenant_id = app_current_tenant_uuid()) "
        "WITH CHECK (tenant_id = app_current_tenant_uuid())"
    )
