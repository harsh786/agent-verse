"""Use a currently supported default embedding model.

Revision ID: 0093_current_embedding_defaults
Revises: 0092_repository_ingestion_leases
"""

from __future__ import annotations

from alembic import op

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_collections "
        "ALTER COLUMN embedder SET DEFAULT 'voyage-4-large'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_collections "
        "ALTER COLUMN embedder SET DEFAULT 'voyage-2'"
    )
