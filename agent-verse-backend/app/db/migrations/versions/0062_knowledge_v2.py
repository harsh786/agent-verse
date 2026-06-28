"""Knowledge Bases v2 — variable-dimension chunk tables with HNSW indexes.

Creates:
  - knowledge_collections: per-tenant collections with embedding model + dim tracking
  - knowledge_chunks_{768,1024,1536,3072}: dimension-specific chunk tables
  - knowledge_documents: document metadata with freshness TTL support

Index type: HNSW with m=16, ef_construction=64.
HNSW works on empty tables (no training data required) and scales well.

Revision ID: 0062
Revises: 0057
Create Date: 2026-06-28
"""
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Ensure pgvector and pg_trgm extensions are available               #
    # ------------------------------------------------------------------ #
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ------------------------------------------------------------------ #
    # Table: knowledge_collections                                        #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_collections (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL,
            name                TEXT NOT NULL,
            description         TEXT,
            domain              TEXT,
            embedding_model     TEXT NOT NULL DEFAULT 'voyage-2',
            embedding_dim       INTEGER NOT NULL DEFAULT 768,
            chunk_size          INTEGER NOT NULL DEFAULT 512,
            chunk_overlap       INTEGER NOT NULL DEFAULT 64,
            language            TEXT NOT NULL DEFAULT 'en',
            is_active           BOOLEAN NOT NULL DEFAULT TRUE,
            is_shared           BOOLEAN NOT NULL DEFAULT FALSE,
            document_count      INTEGER NOT NULL DEFAULT 0,
            chunk_count         INTEGER NOT NULL DEFAULT 0,
            total_size_bytes    BIGINT NOT NULL DEFAULT 0,
            last_indexed_at     TIMESTAMPTZ,
            freshness_ttl_hours INTEGER,
            metadata            JSONB NOT NULL DEFAULT '{}',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_collection_name UNIQUE (tenant_id, name)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_collections_tenant "
        "ON knowledge_collections(tenant_id) WHERE is_active = TRUE"
    )
    op.execute("ALTER TABLE knowledge_collections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_collections FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY knowledge_collections_isolation ON knowledge_collections "
        "USING (tenant_id::text = current_setting('app.tenant_id', TRUE))"
    )

    # ------------------------------------------------------------------ #
    # Tables: knowledge_chunks_{dim} — one per embedding dimension        #
    # Index: HNSW (m=16, ef_construction=64)                             #
    # Works on empty tables; no training data required.                  #
    # ------------------------------------------------------------------ #
    for dim in [768, 1024, 1536, 3072]:
        op.execute(f"""
            CREATE TABLE IF NOT EXISTS knowledge_chunks_{dim} (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id       UUID NOT NULL,
                collection_id   UUID NOT NULL
                                REFERENCES knowledge_collections(id) ON DELETE CASCADE,
                document_id     UUID NOT NULL,
                chunk_index     INTEGER NOT NULL,
                content         TEXT NOT NULL,
                content_hash    TEXT NOT NULL,
                embedding       VECTOR({dim}) NOT NULL,
                metadata        JSONB NOT NULL DEFAULT '{{}}',
                domain_metadata JSONB NOT NULL DEFAULT '{{}}',
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at      TIMESTAMPTZ,
                CONSTRAINT uq_chunk_{dim} UNIQUE (collection_id, document_id, chunk_index)
            )
        """)

        # HNSW index — chosen for empty-table compatibility and query performance.
        op.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_{dim}_vector
            ON knowledge_chunks_{dim}
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """)

        # Composite index for tenant-scoped collection queries
        op.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_{dim}_tenant
            ON knowledge_chunks_{dim}(tenant_id, collection_id)
        """)

        # GIN trigram index for text search (pg_trgm)
        op.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_{dim}_trgm
            ON knowledge_chunks_{dim}
            USING gin (content gin_trgm_ops)
        """)

        # Partial index for freshness filtering
        op.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_{dim}_expires
            ON knowledge_chunks_{dim}(collection_id, expires_at)
            WHERE expires_at IS NOT NULL
        """)

        op.execute(f"ALTER TABLE knowledge_chunks_{dim} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE knowledge_chunks_{dim} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY knowledge_chunks_{dim}_isolation "
            f"ON knowledge_chunks_{dim} "
            f"USING (tenant_id::text = current_setting('app.tenant_id', TRUE))"
        )

    # ------------------------------------------------------------------ #
    # Table: knowledge_documents — document metadata + freshness TTL     #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_documents (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL,
            collection_id       UUID NOT NULL
                                REFERENCES knowledge_collections(id) ON DELETE CASCADE,
            title               TEXT NOT NULL,
            source_url          TEXT,
            source_type         TEXT NOT NULL DEFAULT 'file',
            content_hash        TEXT NOT NULL,
            file_size_bytes     INTEGER,
            mime_type           TEXT,
            language            TEXT,
            status              TEXT NOT NULL DEFAULT 'indexed',
            chunk_count         INTEGER NOT NULL DEFAULT 0,
            error_message       TEXT,
            domain_metadata     JSONB NOT NULL DEFAULT '{}',
            last_modified_at    TIMESTAMPTZ,
            indexed_at          TIMESTAMPTZ,
            expires_at          TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_documents_collection "
        "ON knowledge_documents(tenant_id, collection_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_documents_expires "
        "ON knowledge_documents(tenant_id, expires_at) WHERE expires_at IS NOT NULL"
    )
    op.execute("ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_documents FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY knowledge_documents_isolation ON knowledge_documents "
        "USING (tenant_id::text = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_documents CASCADE")
    for dim in [768, 1024, 1536, 3072]:
        op.execute(f"DROP TABLE IF EXISTS knowledge_chunks_{dim} CASCADE")
    op.execute("DROP TABLE IF EXISTS knowledge_collections CASCADE")
