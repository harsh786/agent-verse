"""add_episodic_and_procedural_memory_tables

Revision ID: 0089_episodic_procedural_memory
Revises: 0088_memory_conflicts
Create Date: 2026-07-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "episodic_memories",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("goal_id", sa.String(32), nullable=False, index=True),
        sa.Column("goal_text", sa.Text, nullable=False),
        sa.Column("action_summary", sa.Text, nullable=False,
                  server_default=sa.text("''")),
        sa.Column("outcome", sa.String(50), nullable=False,
                  server_default=sa.text("'success'")),
        sa.Column("lessons", sa.Text, nullable=False,
                  server_default=sa.text("''")),
        sa.Column("embedding", postgresql.JSONB, nullable=True),
        sa.Column("quality_score", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("steps_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tools_used", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
    op.create_table(
        "procedural_memories",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("goal_pattern", sa.String(200), nullable=False, index=True),
        sa.Column("domain", sa.String(50), nullable=False,
                  server_default=sa.text("'general'"), index=True),
        sa.Column("tool_sequence", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'")),
        sa.Column("use_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("success_rate", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("avg_steps_saved", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("procedural_memories")
    op.drop_table("episodic_memories")
