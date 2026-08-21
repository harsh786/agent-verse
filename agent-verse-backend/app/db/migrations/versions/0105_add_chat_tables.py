"""add chat tables

Revision ID: 0105_add_chat_tables
Revises: 0104_memory_learning
Create Date: 2026-08-15 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0105"
down_revision: str | None = "0104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── chat_session_folders ──────────────────────────────────────────────────
    op.create_table(
        "chat_session_folders",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("color", sa.String(20), nullable=False, server_default="#6366f1"),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_chat_session_folders_tenant", "chat_session_folders", ["tenant_id"])

    # ── chat_sessions ─────────────────────────────────────────────────────────
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("title", sa.String(500), nullable=False, server_default="New Chat"),
        sa.Column("pinned", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("ttl_days", sa.Integer, nullable=True),
        sa.Column("system_prompt", sa.Text, nullable=True),
        sa.Column("agent_id", sa.String(32), nullable=True),
        sa.Column("folder_id", sa.String(32), nullable=True),
        sa.Column("show_reasoning", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("proactive_suggestions", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("preferred_model", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_chat_sessions_tenant", "chat_sessions", ["tenant_id"])
    op.create_index("idx_chat_sessions_pinned", "chat_sessions", ["tenant_id", "pinned"])
    op.create_index("idx_chat_sessions_folder", "chat_sessions", ["folder_id"])
    op.create_index("idx_chat_sessions_updated", "chat_sessions", ["tenant_id", "updated_at"])

    # ── chat_messages ─────────────────────────────────────────────────────────
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("session_id", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),  # user | assistant | system
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("branch_id", sa.String(32), nullable=True),
        sa.Column("parent_message_id", sa.String(32), nullable=True),
        sa.Column("goal_id", sa.String(32), nullable=True),
        sa.Column("intent", sa.String(20), nullable=True),  # QA|GOAL|CLARIFY|SCHEDULE
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_chat_messages_session", "chat_messages", ["session_id"])
    op.create_index("idx_chat_messages_tenant", "chat_messages", ["tenant_id"])
    op.create_index("idx_chat_messages_created", "chat_messages", ["session_id", "created_at"])

    # GIN full-text search index on content
    op.execute(
        """
        CREATE INDEX idx_chat_messages_fts
        ON chat_messages
        USING GIN (to_tsvector('english', content))
        """
    )

    # ── chat_message_usage ────────────────────────────────────────────────────
    op.create_table(
        "chat_message_usage",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("message_id", sa.String(32), nullable=False),
        sa.Column("session_id", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("model", sa.String(100), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_chat_message_usage_session", "chat_message_usage", ["session_id"])
    op.create_index("idx_chat_message_usage_tenant", "chat_message_usage", ["tenant_id"])

    # ── chat_artifacts ────────────────────────────────────────────────────────
    op.create_table(
        "chat_artifacts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("session_id", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("message_id", sa.String(32), nullable=True),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("language", sa.String(50), nullable=False, server_default="text"),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_chat_artifacts_session", "chat_artifacts", ["session_id"])

    # ── View: session usage summary ───────────────────────────────────────────
    op.execute(
        """
        CREATE VIEW chat_session_usage_summary AS
        SELECT
            session_id,
            tenant_id,
            SUM(tokens_in) + SUM(tokens_out) AS total_tokens,
            SUM(tokens_in)                   AS total_tokens_in,
            SUM(tokens_out)                  AS total_tokens_out,
            SUM(cost_usd)                    AS total_cost_usd,
            COUNT(*)                         AS llm_calls
        FROM chat_message_usage
        GROUP BY session_id, tenant_id
        """
    )

    # ── RLS policies ──────────────────────────────────────────────────────────
    for table in (
        "chat_session_folders",
        "chat_sessions",
        "chat_messages",
        "chat_message_usage",
        "chat_artifacts",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.tenant_id', true))
            """
        )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS chat_session_usage_summary")
    for table in (
        "chat_artifacts",
        "chat_message_usage",
        "chat_messages",
        "chat_sessions",
        "chat_session_folders",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("chat_artifacts")
    op.drop_table("chat_message_usage")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("chat_session_folders")
