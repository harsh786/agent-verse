"""0112 — Org artifacts + gateway conversations tables.

Adds:
  - org_artifacts: versioned, lineage-tracked outputs from missions/teams
  - gateway_conversations: multi-turn conversation state across channels

All tables:
  - Are tenant-isolated via RLS policies
  - Have UUID v7 primary keys (time-sortable)
  - Include tenant_id + created_at + updated_at
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0112"
down_revision = "0111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── org_artifacts ─────────────────────────────────────────────────────────
    op.create_table(
        "org_artifacts",
        sa.Column("id",          UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",   UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",      UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("mission_id",  UUID(as_uuid=True), nullable=True),
        sa.Column("team_id",     UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id",    sa.String(200),     nullable=True),
        sa.Column("model_id",    sa.String(200),     nullable=True),
        sa.Column("title",       sa.String(500),     nullable=False),
        sa.Column("kind",        sa.String(50),      nullable=False, server_default="document"),
        # kind: document|code|data|image|report|plan|contract|analysis|presentation
        sa.Column("content",     sa.Text,            nullable=True),
        sa.Column("content_url", sa.String(1000),    nullable=True),
        sa.Column("version",     sa.Integer,         nullable=False, server_default="1"),
        sa.Column("sources",     JSONB,              server_default="[]"),
        sa.Column("reviewers",   JSONB,              server_default="[]"),
        sa.Column("approved_by", sa.String(200),     nullable=True),
        sa.Column("status",      sa.String(50),      nullable=False, server_default="draft"),
        # status: draft|under_review|approved|rejected|archived
        sa.Column("quality_score", sa.Float,         nullable=True),
        sa.Column("lineage",     JSONB,              server_default="{}"),
        sa.Column("tags",        ARRAY(sa.String),   server_default="{}"),
        sa.Column("extra_data",  JSONB,              server_default="{}"),
        sa.Column("created_at",  sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",  sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_org_artifacts_tenant "
        "ON org_artifacts(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_org_artifacts_org "
        "ON org_artifacts(tenant_id, org_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_org_artifacts_mission "
        "ON org_artifacts(tenant_id, mission_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_org_artifacts_status "
        "ON org_artifacts(tenant_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_org_artifacts_kind "
        "ON org_artifacts(tenant_id, kind)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_org_artifacts_created "
        "ON org_artifacts(tenant_id, created_at DESC)"
    )
    op.execute("ALTER TABLE org_artifacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_artifacts FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON org_artifacts
        USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)
    """)

    # ── gateway_conversations ─────────────────────────────────────────────────
    op.create_table(
        "gateway_conversations",
        sa.Column("id",               UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",        UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",           UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        # channel: rest|telegram|slack|whatsapp|discord|email|mcp|a2a|teams|voice
        sa.Column("channel",          sa.String(50),      nullable=False),
        # channel-specific user identity (Telegram user_id, Slack user_id, etc.)
        sa.Column("channel_user_id",  sa.String(500),     nullable=True),
        # channel-specific thread key (Telegram chat_id, Slack thread_ts, email thread_id)
        sa.Column("conversation_key", sa.String(500),     nullable=True),
        # JSONB array of {command, response, timestamp} turn objects
        sa.Column("turns",            JSONB,              server_default="[]"),
        # LLM-compressed summary of older turns (to save context)
        sa.Column("context_summary",  sa.Text,            nullable=True),
        sa.Column("last_command_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gw_conv_tenant "
        "ON gateway_conversations(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gw_conv_org "
        "ON gateway_conversations(tenant_id, org_id)"
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
    op.execute("""
        CREATE POLICY tenant_isolation ON gateway_conversations
        USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON gateway_conversations")
    op.execute("DROP TABLE IF EXISTS gateway_conversations CASCADE")

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON org_artifacts")
    op.execute("DROP TABLE IF EXISTS org_artifacts CASCADE")
