"""Fix oauth_tokens column names and knowledge table UUID→TEXT types.

Revision ID: 0068
Revises: 0067
Create Date: 2026-06-29

Changes:
  - oauth_tokens: rename access_token_enc→access_token, refresh_token_enc→refresh_token
    so app/mcp/oauth.py SQL matches the actual column names.
  - knowledge_collections / knowledge_chunks_* / knowledge_documents:
    migration 0062 defined IDs as UUID but the ORM models use String(32)/TEXT and
    the tenants.id FK is TEXT, causing JOIN type errors. Drop and recreate with TEXT.
"""
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # oauth_tokens — rename encrypted column names to plain names         #
    # The app code (app/mcp/oauth.py) inserts/selects access_token and   #
    # refresh_token; migration 0006 created them as access_token_enc /   #
    # refresh_token_enc.                                                  #
    # ------------------------------------------------------------------ #
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='oauth_tokens' AND column_name='access_token_enc'
            ) THEN
                ALTER TABLE oauth_tokens RENAME COLUMN access_token_enc TO access_token;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='oauth_tokens' AND column_name='refresh_token_enc'
            ) THEN
                ALTER TABLE oauth_tokens RENAME COLUMN refresh_token_enc TO refresh_token;
            END IF;
        END
        $$
    """)

    # ------------------------------------------------------------------ #
    # knowledge tables — drop UUID-typed tables from 0062 and recreate   #
    # with TEXT IDs to match ORM models (String(32)) and tenants.id FK.  #
    # Tables are empty in dev so data loss is acceptable.                 #
    # ------------------------------------------------------------------ #
    op.execute("DROP TABLE IF EXISTS knowledge_documents CASCADE")
    for dim in [768, 1024, 1536, 3072]:
        op.execute(f"DROP TABLE IF EXISTS knowledge_chunks_{dim} CASCADE")
    op.execute("DROP TABLE IF EXISTS knowledge_collections CASCADE")

    # Recreate knowledge_collections with TEXT IDs
    op.execute("""
        CREATE TABLE knowledge_collections (
            id                  TEXT NOT NULL DEFAULT replace(gen_random_uuid()::text, '-', ''),
            tenant_id           TEXT NOT NULL,
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
            CONSTRAINT uq_collection_name UNIQUE (tenant_id, name),
            PRIMARY KEY (id)
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
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )

    # Recreate knowledge_chunks_* with TEXT IDs
    for dim in [768, 1024, 1536, 3072]:
        op.execute(f"""
            CREATE TABLE knowledge_chunks_{dim} (
                id              TEXT NOT NULL DEFAULT replace(gen_random_uuid()::text, '-', ''),
                tenant_id       TEXT NOT NULL,
                collection_id   TEXT NOT NULL
                                REFERENCES knowledge_collections(id) ON DELETE CASCADE,
                document_id     TEXT NOT NULL,
                chunk_index     INTEGER NOT NULL,
                content         TEXT NOT NULL,
                content_hash    TEXT NOT NULL,
                embedding       VECTOR({dim}) NOT NULL,
                metadata        JSONB NOT NULL DEFAULT '{{}}',
                domain_metadata JSONB NOT NULL DEFAULT '{{}}',
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at      TIMESTAMPTZ,
                PRIMARY KEY (id),
                CONSTRAINT uq_chunk_{dim} UNIQUE (collection_id, document_id, chunk_index)
            )
        """)
        if dim <= 2000:
            op.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_{dim}_vector
                ON knowledge_chunks_{dim}
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """)
        op.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_{dim}_tenant
            ON knowledge_chunks_{dim}(tenant_id, collection_id)
        """)
        op.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_{dim}_trgm
            ON knowledge_chunks_{dim}
            USING gin (content gin_trgm_ops)
        """)
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
            f"USING (tenant_id = current_setting('app.tenant_id', TRUE))"
        )

    # Recreate knowledge_documents with TEXT IDs
    op.execute("""
        CREATE TABLE knowledge_documents (
            id                  TEXT NOT NULL DEFAULT replace(gen_random_uuid()::text, '-', ''),
            tenant_id           TEXT NOT NULL,
            collection_id       TEXT NOT NULL
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
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id)
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
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_documents CASCADE")
    for dim in [768, 1024, 1536, 3072]:
        op.execute(f"DROP TABLE IF EXISTS knowledge_chunks_{dim} CASCADE")
    op.execute("DROP TABLE IF EXISTS knowledge_collections CASCADE")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='oauth_tokens' AND column_name='access_token'
            ) THEN
                ALTER TABLE oauth_tokens RENAME COLUMN access_token TO access_token_enc;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='oauth_tokens' AND column_name='refresh_token'
            ) THEN
                ALTER TABLE oauth_tokens RENAME COLUMN refresh_token TO refresh_token_enc;
            END IF;
        END
        $$
    """)
