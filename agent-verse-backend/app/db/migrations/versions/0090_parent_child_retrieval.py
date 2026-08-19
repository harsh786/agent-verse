"""add_parent_child_and_sentence_window_columns

Revision ID: 0090_parent_child_retrieval
Revises: 0089_episodic_procedural_memory
Create Date: 2026-07-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add parent_chunk_id to knowledge_chunks_1536 (and common dims)
    for table in ["knowledge_chunks_1536", "knowledge_chunks_1024", "knowledge_chunks_768"]:
        try:
            op.add_column(
                table,
                sa.Column("parent_chunk_id", sa.String(32), nullable=True),
            )
            op.create_index(
                f"ix_{table}_parent_chunk_id",
                table,
                ["parent_chunk_id"],
            )
        except Exception:
            pass  # Table may not exist for this dim
        try:
            op.add_column(
                table,
                sa.Column(
                    "chunk_level",
                    sa.String(10),
                    nullable=True,
                    server_default="'leaf'",
                ),
            )
        except Exception:
            pass
        try:
            op.add_column(
                table,
                sa.Column("window_start", sa.Integer, nullable=True),
            )
        except Exception:
            pass
        try:
            op.add_column(
                table,
                sa.Column("window_end", sa.Integer, nullable=True),
            )
        except Exception:
            pass


def downgrade() -> None:
    for table in ["knowledge_chunks_1536", "knowledge_chunks_1024", "knowledge_chunks_768"]:
        try:
            op.drop_index(f"ix_{table}_parent_chunk_id", table_name=table)
        except Exception:
            pass
        try:
            op.drop_column(table, "parent_chunk_id")
        except Exception:
            pass
        try:
            op.drop_column(table, "chunk_level")
        except Exception:
            pass
        try:
            op.drop_column(table, "window_start")
        except Exception:
            pass
        try:
            op.drop_column(table, "window_end")
        except Exception:
            pass
