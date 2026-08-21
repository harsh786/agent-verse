"""add_runtime_profile_fields_to_goals

Revision ID: 0086_runtime_profile_fields
Revises: 0085_add_knowledge_graph
Create Date: 2026-07-08

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE goals
        ADD COLUMN IF NOT EXISTS runtime_profile_id VARCHAR(64),
        ADD COLUMN IF NOT EXISTS patterns_used JSONB DEFAULT '[]'::jsonb,
        ADD COLUMN IF NOT EXISTS rag_strategy_used VARCHAR(64) DEFAULT ''
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE goals
        DROP COLUMN IF EXISTS runtime_profile_id,
        DROP COLUMN IF EXISTS patterns_used,
        DROP COLUMN IF EXISTS rag_strategy_used
    """)
