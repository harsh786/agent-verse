"""Rename knowledge_collections.embedding_model to embedder to match ORM model.

Revision ID: 0069
Revises: 0068
Create Date: 2026-06-29

The ORM model (app/db/models/knowledge.py) defines the column as `embedder`
but migrations 0062/0068 created it as `embedding_model`.
"""
from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'knowledge_collections'
                  AND column_name = 'embedding_model'
            ) THEN
                ALTER TABLE knowledge_collections
                    RENAME COLUMN embedding_model TO embedder;
            END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'knowledge_collections'
                  AND column_name = 'embedder'
            ) THEN
                ALTER TABLE knowledge_collections
                    RENAME COLUMN embedder TO embedding_model;
            END IF;
        END
        $$
    """)
