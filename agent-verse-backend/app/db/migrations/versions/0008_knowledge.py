"""Create knowledge_collections, documents, execution_memory, and long_term_memory tables.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── knowledge_collections ─────────────────────────────────────────────
    op.create_table(
        "knowledge_collections",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True, server_default=""),
        sa.Column("embedder", sa.String(100), nullable=True, server_default="voyage"),
        sa.Column(
            "document_count", sa.Integer, nullable=True, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_knowledge_collections_tenant_id", "knowledge_collections", ["tenant_id"]
    )

    op.execute("ALTER TABLE knowledge_collections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_collections FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY knowledge_collections_tenant_isolation ON knowledge_collections "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ── documents (with pgvector embedding) ───────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "collection_id",
            sa.String(32),
            sa.ForeignKey("knowledge_collections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column(
            "chunk_index", sa.Integer, nullable=True, server_default=sa.text("0")
        ),
        sa.Column(
            "metadata", JSONB, nullable=True, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_documents_collection_id", "documents", ["collection_id"])
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"])

    # HNSW index for cosine similarity search (requires pgvector extension from 0001)
    op.execute(
        "CREATE INDEX idx_documents_embedding ON documents "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m=16, ef_construction=64)"
    )
    # GIN trigram index for full-text search (requires pg_trgm from 0001)
    op.execute(
        "CREATE INDEX idx_documents_content_trgm ON documents "
        "USING GIN (content gin_trgm_ops)"
    )

    op.execute("ALTER TABLE documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY documents_tenant_isolation ON documents "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ── execution_memory ─────────────────────────────────────────────────
    op.create_table(
        "execution_memory",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("goal_text", sa.Text, nullable=False),
        sa.Column("plan", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "success", sa.Boolean, nullable=False, server_default=sa.text("TRUE")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_execution_memory_tenant_id", "execution_memory", ["tenant_id"])

    op.execute("ALTER TABLE execution_memory ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE execution_memory FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY execution_memory_tenant_isolation ON execution_memory "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ── long_term_memory ─────────────────────────────────────────────────
    op.create_table(
        "long_term_memory",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("source_goal_id", sa.String(32), nullable=True, server_default=""),
        sa.Column(
            "memory_type",
            sa.String(50),
            nullable=True,
            server_default="success_pattern",
        ),
        sa.Column(
            "confidence", sa.Float, nullable=True, server_default=sa.text("1.0")
        ),
        sa.Column("tags", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_long_term_memory_tenant_id", "long_term_memory", ["tenant_id"])
    op.create_index(
        "ix_long_term_memory_memory_type", "long_term_memory", ["memory_type"]
    )

    op.execute("ALTER TABLE long_term_memory ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE long_term_memory FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY long_term_memory_tenant_isolation ON long_term_memory "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS long_term_memory_tenant_isolation ON long_term_memory"
    )
    op.drop_table("long_term_memory")

    op.execute(
        "DROP POLICY IF EXISTS execution_memory_tenant_isolation ON execution_memory"
    )
    op.drop_table("execution_memory")

    op.execute("DROP POLICY IF EXISTS documents_tenant_isolation ON documents")
    op.execute("DROP INDEX IF EXISTS idx_documents_content_trgm")
    op.execute("DROP INDEX IF EXISTS idx_documents_embedding")
    op.drop_table("documents")

    op.execute(
        "DROP POLICY IF EXISTS knowledge_collections_tenant_isolation "
        "ON knowledge_collections"
    )
    op.drop_table("knowledge_collections")
