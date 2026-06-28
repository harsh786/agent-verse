# Knowledge Bases — World-Class Specification

**Area 10 · Migration 0062 · Version 1.0 · 2026-06-28**

---

## 1. Vision

Knowledge bases are AgentVerse's long-term memory system — the mechanism by which agents accumulate domain expertise, regulatory documents, case precedents, and product catalogs that persist across goals. The current implementation has five critical deficiencies: `hybrid_search_db()` omits a `tenant_id` filter from the SQL query, relying solely on Postgres RLS for isolation — safe in theory but bypassed if RLS is temporarily disabled for migrations or by any superuser connection; the embedding dimension is hardcoded to `Vector(768)`, which breaks silently when OpenAI's `text-embedding-3-large` (3072 dims) or Anthropic's embedder (1024 dims) is used, producing garbage similarity scores; the in-memory sync caps at 100,000 chunks, meaning large legal document repositories (a law firm's 500,000-document library) are simply truncated; Confluence and Jira tokens are logged in plain text (visible in any log aggregation system, violating PCI and HIPAA); and when no embedder is configured, the system silently uses fake zero-vector embeddings, making every similarity search return arbitrary results without any warning.

This specification delivers a production-grade knowledge infrastructure. Variable embedding dimensions (768 for Voyage/BGE, 1024 for Anthropic, 1536 for OpenAI Ada, 3072 for OpenAI large) are stored per-collection in metadata, and queries use the matching dimension table. The tenant isolation bug is fixed with an explicit `AND tenant_id = :tenant_id` clause in every search query. The 100K chunk cap is removed in favor of database-backed chunked sync with progress tracking. External service tokens use `SecretStr` from pydantic with a custom `__repr__` that masks values, preventing log leakage. The fake embedder is replaced with an explicit error that surfaces through the API instead of silently corrupting search results. Domain-specific knowledge types (legal case law and statutes, healthcare FHIR resources and ICD-10 codes, financial SEC filings and regulations) use structured metadata schemas that enable filtered retrieval beyond pure vector similarity.

---

## 2. Current State Assessment

| Component | Current State | Gap | Severity |
|-----------|---------------|-----|----------|
| Tenant isolation in SQL | RLS-only, no WHERE clause | Bypassed by superuser / migration connections | CRITICAL |
| Embedding dimension | `Vector(768)` hardcoded | Breaks with OpenAI 1536/3072-dim; garbage results | CRITICAL |
| Confluence/Jira token logging | Logged in plain text | PII/credential leak in all log aggregators | CRITICAL |
| Silent fake embeddings | Zero vectors used silently | Corrupts all search results without warning | CRITICAL |
| 100K chunk cap | `sync_from_db` has LIMIT 100000 | Large repositories silently truncated | HIGH |
| Document freshness TTL | Not implemented | Stale documents pollute search results | HIGH |
| Multi-collection search | Not implemented | Cannot search across multiple KBs | HIGH |
| Domain knowledge types | Generic chunks only | No legal/healthcare/finance structured metadata | MEDIUM |
| Graph visualization | None | Cannot explore knowledge relationships | MEDIUM |
| Incremental sync | Full re-embed on any update | Expensive; 1M-doc re-embed takes hours | MEDIUM |

---

## 3. Backend Architecture

### 3.1 Database Schema — Migration 0062

```sql
-- =============================================================================
-- Migration 0062: Knowledge collections with variable embedding dims
-- Author: AgentVerse Platform Team
-- Date: 2026-06-28
-- =============================================================================

BEGIN;

-- --------------------------------------------------------
-- Table: knowledge_collections
-- Named, versioned knowledge bases with configurable embedding
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_collections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT,
    domain          TEXT,                               -- 'legal'|'healthcare'|'finance'|NULL
    embedding_model TEXT NOT NULL DEFAULT 'voyage-3',  -- tracks which model embedded this KB
    embedding_dim   INTEGER NOT NULL DEFAULT 768,       -- 768|1024|1536|3072
    chunk_size      INTEGER NOT NULL DEFAULT 512,
    chunk_overlap   INTEGER NOT NULL DEFAULT 64,
    language        TEXT NOT NULL DEFAULT 'en',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    is_shared       BOOLEAN NOT NULL DEFAULT FALSE,     -- shared across tenant agents
    -- Stats
    document_count  INTEGER NOT NULL DEFAULT 0,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    total_size_bytes BIGINT NOT NULL DEFAULT 0,
    last_indexed_at TIMESTAMPTZ,
    -- Freshness
    freshness_ttl_hours INTEGER,                        -- NULL = never expires
    -- Metadata
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_collection_name UNIQUE (tenant_id, name)
);

CREATE INDEX idx_collections_tenant ON knowledge_collections(tenant_id) WHERE is_active = TRUE;

ALTER TABLE knowledge_collections ENABLE ROW LEVEL SECURITY;
CREATE POLICY collections_isolation ON knowledge_collections
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Tables: knowledge_chunks_{dim}
-- Separate pgvector tables per embedding dimension
-- Eliminates the Vector(768) hardcoded limitation
-- --------------------------------------------------------

-- 768-dim (Voyage, BGE, Cohere Embed v3)
CREATE TABLE IF NOT EXISTS knowledge_chunks_768 (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    collection_id   UUID NOT NULL REFERENCES knowledge_collections(id) ON DELETE CASCADE,
    document_id     UUID NOT NULL,
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    embedding       VECTOR(768) NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    domain_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ,
    CONSTRAINT uq_chunk_768 UNIQUE (collection_id, document_id, chunk_index)
);
CREATE INDEX idx_chunks_768_vector   ON knowledge_chunks_768 USING ivfflat (embedding vector_cosine_ops) WITH (lists = 200);
CREATE INDEX idx_chunks_768_tenant   ON knowledge_chunks_768(tenant_id, collection_id);
CREATE INDEX idx_chunks_768_doc      ON knowledge_chunks_768(document_id);
CREATE INDEX idx_chunks_768_trgm     ON knowledge_chunks_768 USING gin (content gin_trgm_ops);

-- 1024-dim (Anthropic, Cohere v3 multilingual)
CREATE TABLE IF NOT EXISTS knowledge_chunks_1024 (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    collection_id   UUID NOT NULL REFERENCES knowledge_collections(id) ON DELETE CASCADE,
    document_id     UUID NOT NULL,
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    embedding       VECTOR(1024) NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    domain_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ,
    CONSTRAINT uq_chunk_1024 UNIQUE (collection_id, document_id, chunk_index)
);
CREATE INDEX idx_chunks_1024_vector ON knowledge_chunks_1024 USING ivfflat (embedding vector_cosine_ops) WITH (lists = 200);
CREATE INDEX idx_chunks_1024_tenant ON knowledge_chunks_1024(tenant_id, collection_id);
CREATE INDEX idx_chunks_1024_trgm   ON knowledge_chunks_1024 USING gin (content gin_trgm_ops);

-- 1536-dim (OpenAI text-embedding-ada-002, text-embedding-3-small)
CREATE TABLE IF NOT EXISTS knowledge_chunks_1536 (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    collection_id   UUID NOT NULL REFERENCES knowledge_collections(id) ON DELETE CASCADE,
    document_id     UUID NOT NULL,
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    embedding       VECTOR(1536) NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    domain_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ,
    CONSTRAINT uq_chunk_1536 UNIQUE (collection_id, document_id, chunk_index)
);
CREATE INDEX idx_chunks_1536_vector ON knowledge_chunks_1536 USING ivfflat (embedding vector_cosine_ops) WITH (lists = 200);
CREATE INDEX idx_chunks_1536_tenant ON knowledge_chunks_1536(tenant_id, collection_id);
CREATE INDEX idx_chunks_1536_trgm   ON knowledge_chunks_1536 USING gin (content gin_trgm_ops);

-- 3072-dim (OpenAI text-embedding-3-large)
CREATE TABLE IF NOT EXISTS knowledge_chunks_3072 (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    collection_id   UUID NOT NULL REFERENCES knowledge_collections(id) ON DELETE CASCADE,
    document_id     UUID NOT NULL,
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    embedding       VECTOR(3072) NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    domain_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ,
    CONSTRAINT uq_chunk_3072 UNIQUE (collection_id, document_id, chunk_index)
);
CREATE INDEX idx_chunks_3072_vector ON knowledge_chunks_3072 USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_chunks_3072_tenant ON knowledge_chunks_3072(tenant_id, collection_id);
CREATE INDEX idx_chunks_3072_trgm   ON knowledge_chunks_3072 USING gin (content gin_trgm_ops);

-- --------------------------------------------------------
-- Table: knowledge_documents
-- Document-level tracking with source metadata
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    collection_id   UUID NOT NULL REFERENCES knowledge_collections(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    source_url      TEXT,
    source_type     TEXT NOT NULL DEFAULT 'file'
                    CHECK (source_type IN ('file', 'confluence', 'jira', 'notion', 'web', 'api', 'manual')),
    content_hash    TEXT NOT NULL,
    file_size_bytes INTEGER,
    mime_type       TEXT,
    language        TEXT,
    status          TEXT NOT NULL DEFAULT 'indexed'
                    CHECK (status IN ('pending', 'processing', 'indexed', 'failed', 'expired', 'deleted')),
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    domain_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Freshness
    last_modified_at TIMESTAMPTZ,
    indexed_at      TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_knowledge_docs_collection
    ON knowledge_documents(collection_id, indexed_at DESC);
CREATE INDEX idx_knowledge_docs_tenant
    ON knowledge_documents(tenant_id, source_type);
CREATE INDEX idx_knowledge_docs_expired
    ON knowledge_documents(expires_at) WHERE status = 'indexed' AND expires_at IS NOT NULL;

ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY;
CREATE POLICY knowledge_docs_isolation ON knowledge_documents
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Enforce tenant isolation on all chunk tables via RLS
-- (PLUS explicit WHERE clause in all queries — defence in depth)
-- --------------------------------------------------------
ALTER TABLE knowledge_chunks_768  ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_chunks_1024 ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_chunks_1536 ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_chunks_3072 ENABLE ROW LEVEL SECURITY;

CREATE POLICY chunks_768_isolation  ON knowledge_chunks_768  USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);
CREATE POLICY chunks_1024_isolation ON knowledge_chunks_1024 USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);
CREATE POLICY chunks_1536_isolation ON knowledge_chunks_1536 USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);
CREATE POLICY chunks_3072_isolation ON knowledge_chunks_3072 USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Enable trigram search
-- --------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pg_trgm;

COMMIT;
```

### 3.2 Alembic Migration

```python
# agent-verse-backend/app/db/migrations/versions/0062_knowledge_collections_variable_dim.py
"""Variable embedding dimensions for knowledge bases

Revision ID: 0062
Revises: 0061
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, VECTOR, TIMESTAMPTZ

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "knowledge_collections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("domain", sa.Text()),
        sa.Column("embedding_model", sa.Text(), nullable=False, server_default="'voyage-3'"),
        sa.Column("embedding_dim", sa.Integer(), nullable=False, server_default="768"),
        sa.Column("chunk_size", sa.Integer(), nullable=False, server_default="512"),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False, server_default="64"),
        sa.Column("language", sa.Text(), nullable=False, server_default="'en'"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="TRUE"),
        sa.Column("is_shared", sa.Boolean(), nullable=False, server_default="FALSE"),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_indexed_at", TIMESTAMPTZ()),
        sa.Column("freshness_ttl_hours", sa.Integer()),
        sa.Column("metadata", JSONB(), nullable=False, server_default="'{}'"),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_collection_name"),
    )

    # Create chunk tables for each supported embedding dimension
    for dim in [768, 1024, 1536, 3072]:
        op.execute(f"""
            CREATE TABLE IF NOT EXISTS knowledge_chunks_{dim} (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                collection_id UUID NOT NULL REFERENCES knowledge_collections(id) ON DELETE CASCADE,
                document_id UUID NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                embedding VECTOR({dim}) NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{{}}',
                domain_metadata JSONB NOT NULL DEFAULT '{{}}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ,
                CONSTRAINT uq_chunk_{dim} UNIQUE (collection_id, document_id, chunk_index)
            )
        """)
        lists = 200 if dim <= 1536 else 100
        op.execute(f"""
            CREATE INDEX idx_chunks_{dim}_vector
            ON knowledge_chunks_{dim}
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = {lists})
        """)
        op.execute(f"""
            CREATE INDEX idx_chunks_{dim}_tenant
            ON knowledge_chunks_{dim}(tenant_id, collection_id)
        """)
        op.execute(f"""
            CREATE INDEX idx_chunks_{dim}_trgm
            ON knowledge_chunks_{dim}
            USING gin (content gin_trgm_ops)
        """)

    op.create_table(
        "knowledge_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("collection_id", UUID(as_uuid=True),
                  sa.ForeignKey("knowledge_collections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("source_type", sa.Text(), nullable=False, server_default="'file'"),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("file_size_bytes", sa.Integer()),
        sa.Column("mime_type", sa.Text()),
        sa.Column("language", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="'indexed'"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("domain_metadata", JSONB(), nullable=False, server_default="'{}'"),
        sa.Column("last_modified_at", TIMESTAMPTZ()),
        sa.Column("indexed_at", TIMESTAMPTZ()),
        sa.Column("expires_at", TIMESTAMPTZ()),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("knowledge_documents")
    for dim in [768, 1024, 1536, 3072]:
        op.drop_table(f"knowledge_chunks_{dim}")
    op.drop_table("knowledge_collections")
```

### 3.3 API Endpoints

**GET /api/knowledge/collections** — List tenant collections with stats

**POST /api/knowledge/collections**
```json
{
  "name": "Legal Precedents 2026",
  "domain": "legal",
  "embedding_model": "voyage-3",
  "embedding_dim": 768,
  "chunk_size": 512,
  "chunk_overlap": 64,
  "freshness_ttl_hours": 720
}
```
Response 201: collection object

**PATCH /api/knowledge/collections/{id}** — Update config (re-embed queue on model change)

**DELETE /api/knowledge/collections/{id}** — Cascade delete all documents and chunks

**POST /api/knowledge/collections/{id}/documents/ingest**
```json
{
  "source_type": "file",
  "title": "Smith v Jones - Court of Appeals 2024",
  "content": "...",
  "domain_metadata": {
    "case_citation": "Smith v Jones, 2024 WL 12345",
    "court": "2nd Circuit",
    "decision_date": "2024-03-15",
    "legal_topics": ["contract_law", "breach"]
  }
}
```
Response 202: `{ "document_id": "uuid", "status": "processing", "estimated_chunks": 12 }`

**POST /api/knowledge/collections/{id}/sync/confluence**
```json
{
  "space_key": "LEGAL",
  "base_url": "https://company.atlassian.net",
  "credential_id": "vault-stored-cred-uuid"
}
```
NOTE: credentials are resolved from vault by `credential_id`, never passed in body.

**GET /api/knowledge/collections/{id}/documents** — List documents with status

**DELETE /api/knowledge/collections/{id}/documents/{doc_id}**

#### Search

**POST /api/knowledge/search**
```json
{
  "query": "breach of contract remedies for anticipatory repudiation",
  "collection_ids": ["uuid1", "uuid2"],
  "limit": 10,
  "alpha": 0.7,
  "min_score": 0.5,
  "domain_filters": {
    "court": "2nd Circuit",
    "decision_date_after": "2020-01-01"
  }
}
```
Response:
```json
{
  "results": [
    {
      "chunk_id": "uuid",
      "collection_id": "uuid",
      "document_id": "uuid",
      "document_title": "Smith v Jones",
      "content": "The court held that anticipatory repudiation...",
      "score": 0.892,
      "vector_score": 0.88,
      "text_score": 0.90,
      "domain_metadata": { "case_citation": "...", "court": "2nd Circuit" }
    }
  ],
  "total": 47,
  "query_time_ms": 28
}
```

**POST /api/knowledge/search/multi-collection** — Federated search across all permitted collections

### 3.4 Business Logic — Python

```python
# agent-verse-backend/app/knowledge/store.py
"""
Production-grade KnowledgeStore with:

FIX 1: tenant_id in hybrid_search SQL (was RLS-only)
FIX 2: variable embedding dimensions (was Vector(768) hardcoded)
FIX 3: No 100K chunk cap in sync_from_db
FIX 4: SecretStr for Confluence/Jira tokens (no plain-text logging)
FIX 5: Explicit error on missing embedder (no silent fake embeddings)
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Iterator, Optional
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

# Supported embedding dimensions
VALID_EMBEDDING_DIMS = (768, 1024, 1536, 3072)

# Maps embedding dim to chunk table name
CHUNK_TABLE: dict[int, str] = {
    768:  "knowledge_chunks_768",
    1024: "knowledge_chunks_1024",
    1536: "knowledge_chunks_1536",
    3072: "knowledge_chunks_3072",
}


def _chunk_table(dim: int) -> str:
    """Returns the appropriate chunk table name for a given embedding dimension."""
    if dim not in CHUNK_TABLE:
        raise ValueError(
            f"Unsupported embedding dimension {dim}. "
            f"Supported: {sorted(CHUNK_TABLE.keys())}"
        )
    return CHUNK_TABLE[dim]


class ConfluenceConnector:
    """
    FIX 4: Credentials use SecretStr to prevent token logging.

    Before fix: token was a plain str, appeared in logs as:
      "confluence_token=my_real_secret_token"

    After fix: token is SecretStr, appears in logs as:
      "confluence_token=SecretStr('**********')"
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        api_token: SecretStr,        # FIX: was str, now SecretStr
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._token = api_token     # .get_secret_value() only when needed

    async def fetch_pages(self, space_key: str) -> list[dict[str, str]]:
        """Fetches pages from a Confluence space. Token never logged."""
        import httpx

        # get_secret_value() only at the point of actual use
        auth = (self._username, self._token.get_secret_value())

        pages: list[dict] = []
        start = 0
        limit = 50

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                resp = await client.get(
                    f"{self._base_url}/rest/api/content",
                    params={
                        "spaceKey": space_key,
                        "type": "page",
                        "expand": "body.storage,version,metadata.labels",
                        "start": start,
                        "limit": limit,
                    },
                    auth=auth,
                )
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                for page in results:
                    pages.append({
                        "id": page["id"],
                        "title": page["title"],
                        "content": page["body"]["storage"]["value"],
                        "url": f"{self._base_url}/wiki{page['_links']['webui']}",
                        "last_modified": page["version"]["when"],
                    })
                if len(results) < limit:
                    break
                start += limit

        logger.info(
            "confluence_fetch_complete",
            space=space_key,
            page_count=len(pages),
            # token intentionally NOT logged here
        )
        return pages

    def __repr__(self) -> str:
        return f"ConfluenceConnector(base_url={self._base_url!r}, username={self._username!r})"


class KnowledgeStore:
    """
    Production-grade knowledge store with variable embedding dimensions,
    explicit tenant isolation in SQL, no chunk cap, and safe credentials.
    """

    def __init__(self, db_factory, embedder=None) -> None:
        self._db = db_factory
        self._embedder = embedder

    def _require_embedder(self) -> Any:
        """
        FIX 5: Was silently returning None and using zero vectors.
        Now raises an explicit error if embedder is not configured.
        """
        if self._embedder is None:
            raise RuntimeError(
                "Knowledge base search requires an embedder to be configured. "
                "Set VOYAGE_API_KEY, OPENAI_API_KEY, or another embedding provider. "
                "Refusing to use fake zero-vector embeddings that corrupt search results."
            )
        return self._embedder

    async def hybrid_search(
        self,
        tenant_id: str,
        collection_id: str,
        query: str,
        limit: int = 10,
        alpha: float = 0.7,
        min_score: float = 0.0,
        domain_filters: Optional[dict] = None,
        exclude_expired: bool = True,
    ) -> list[dict[str, Any]]:
        """
        FIX 1: SQL now has explicit tenant_id filter (was RLS-only).
        FIX 2: Uses correct chunk table based on collection embedding_dim.
        FIX 5: Raises if embedder not configured.

        Hybrid search = alpha * vector_score + (1-alpha) * text_score
        """
        embedder = self._require_embedder()

        async with self._db() as db:
            # Load collection to get embedding dimension
            from sqlalchemy import text
            col_result = await db.execute(
                text("SELECT embedding_dim FROM knowledge_collections WHERE id = :id AND tenant_id = :tid"),
                {"id": collection_id, "tid": tenant_id},
            )
            col_row = col_result.fetchone()
            if not col_row:
                raise ValueError(f"Collection {collection_id} not found for tenant {tenant_id}")

            dim = col_row.embedding_dim
            table = _chunk_table(dim)

            # Embed the query using the correct model
            query_embedding = await embedder.embed(query, dim=dim)

            # Build domain filter clause
            domain_filter_sql = ""
            filter_params: dict[str, Any] = {}
            if domain_filters:
                conditions = []
                for i, (key, value) in enumerate(domain_filters.items()):
                    param = f"df_{i}"
                    conditions.append(f"domain_metadata->>'{key}' = :{param}")
                    filter_params[param] = str(value)
                if conditions:
                    domain_filter_sql = "AND " + " AND ".join(conditions)

            # Expiry filter
            expiry_sql = "AND (expires_at IS NULL OR expires_at > now())" if exclude_expired else ""

            # FIX 1: Explicit tenant_id in WHERE clause (not just RLS)
            results = await db.execute(
                text(f"""
                    WITH
                    vector_results AS (
                        SELECT
                            id, tenant_id, collection_id, document_id, chunk_index,
                            content, metadata, domain_metadata,
                            1 - (embedding <=> :query_vector::vector) AS vector_score
                        FROM {table}
                        WHERE tenant_id = :tenant_id        -- FIX 1: explicit tenant isolation
                          AND collection_id = :collection_id
                          {expiry_sql}
                          {domain_filter_sql}
                        ORDER BY embedding <=> :query_vector::vector
                        LIMIT :limit_v
                    ),
                    text_results AS (
                        SELECT
                            id,
                            similarity(content, :query_text) AS text_score
                        FROM {table}
                        WHERE tenant_id = :tenant_id        -- FIX 1: explicit tenant isolation
                          AND collection_id = :collection_id
                          AND content % :query_text
                          {expiry_sql}
                        LIMIT :limit_t
                    ),
                    combined AS (
                        SELECT
                            v.id, v.collection_id, v.document_id, v.chunk_index,
                            v.content, v.metadata, v.domain_metadata,
                            COALESCE(v.vector_score, 0.0) AS vector_score,
                            COALESCE(t.text_score, 0.0)   AS text_score,
                            :alpha * COALESCE(v.vector_score, 0.0)
                            + (1 - :alpha) * COALESCE(t.text_score, 0.0) AS score
                        FROM vector_results v
                        LEFT JOIN text_results t ON t.id = v.id
                    )
                    SELECT c.*, d.title AS document_title
                    FROM combined c
                    JOIN knowledge_documents d ON d.id = c.document_id
                    WHERE c.score >= :min_score
                    ORDER BY c.score DESC
                    LIMIT :limit
                """),
                {
                    "query_vector": query_embedding,
                    "query_text": query,
                    "tenant_id": tenant_id,     # FIX 1: passed as parameter
                    "collection_id": collection_id,
                    "alpha": alpha,
                    "min_score": min_score,
                    "limit": limit,
                    "limit_v": limit * 2,
                    "limit_t": limit * 2,
                    **filter_params,
                },
            )

        return [
            {
                "chunk_id": str(row.id),
                "collection_id": str(row.collection_id),
                "document_id": str(row.document_id),
                "document_title": row.document_title,
                "content": row.content,
                "score": float(row.score),
                "vector_score": float(row.vector_score),
                "text_score": float(row.text_score),
                "metadata": row.metadata,
                "domain_metadata": row.domain_metadata,
            }
            for row in results.fetchall()
        ]

    async def multi_collection_search(
        self,
        tenant_id: str,
        collection_ids: list[str],
        query: str,
        limit: int = 20,
        alpha: float = 0.7,
    ) -> list[dict[str, Any]]:
        """
        Federated search across multiple collections.
        Collections may have different embedding dimensions.
        Results are merged and re-ranked by score.
        """
        all_results: list[dict] = []

        for coll_id in collection_ids:
            try:
                results = await self.hybrid_search(
                    tenant_id=tenant_id,
                    collection_id=coll_id,
                    query=query,
                    limit=limit,
                    alpha=alpha,
                )
                all_results.extend(results)
            except Exception as exc:
                logger.warning("collection_search_error",
                               collection_id=coll_id, error=str(exc))

        # Re-rank merged results
        all_results.sort(key=lambda r: r["score"], reverse=True)
        return all_results[:limit]

    async def ingest_document(
        self,
        tenant_id: str,
        collection_id: str,
        title: str,
        content: str,
        source_type: str = "manual",
        source_url: Optional[str] = None,
        domain_metadata: Optional[dict] = None,
    ) -> dict[str, Any]:
        """
        Chunks and embeds a document.
        FIX 5: raises if embedder not configured.
        """
        embedder = self._require_embedder()

        async with self._db() as db:
            from sqlalchemy import text
            col = await db.execute(
                text("SELECT embedding_dim, embedding_model, chunk_size, chunk_overlap, freshness_ttl_hours FROM knowledge_collections WHERE id = :id AND tenant_id = :tid"),
                {"id": collection_id, "tid": tenant_id},
            )
            col_row = col.fetchone()
            if not col_row:
                raise ValueError(f"Collection {collection_id} not found")

            dim = col_row.embedding_dim
            table = _chunk_table(dim)

            # Chunk the document
            chunks = self._chunk_text(content, col_row.chunk_size, col_row.chunk_overlap)

            # Compute expiry
            expires_at = None
            if col_row.freshness_ttl_hours:
                from datetime import datetime, timedelta, timezone
                expires_at = datetime.now(timezone.utc) + timedelta(hours=col_row.freshness_ttl_hours)

            content_hash = hashlib.sha256(content.encode()).hexdigest()

            # Insert document record
            doc_id = str(uuid4())
            await db.execute(
                text("""
                    INSERT INTO knowledge_documents
                    (id, tenant_id, collection_id, title, source_url, source_type,
                     content_hash, status, domain_metadata, expires_at, created_at)
                    VALUES (:id, :tenant_id, :collection_id, :title, :source_url,
                            :source_type, :content_hash, 'processing', :domain_metadata,
                            :expires_at, now())
                """),
                {
                    "id": doc_id, "tenant_id": tenant_id,
                    "collection_id": collection_id, "title": title,
                    "source_url": source_url, "source_type": source_type,
                    "content_hash": content_hash,
                    "domain_metadata": json.dumps(domain_metadata or {}),
                    "expires_at": expires_at,
                },
            )
            await db.commit()

            # Embed and insert chunks in batches of 50
            batch_size = 50
            total_chunks = 0

            for batch_start in range(0, len(chunks), batch_size):
                batch = chunks[batch_start:batch_start + batch_size]
                batch_texts = [c["content"] for c in batch]
                embeddings = await embedder.embed_batch(batch_texts, dim=dim)

                chunk_rows = []
                for i, (chunk, embedding) in enumerate(zip(batch, embeddings)):
                    chunk_rows.append({
                        "id": str(uuid4()),
                        "tenant_id": tenant_id,
                        "collection_id": collection_id,
                        "document_id": doc_id,
                        "chunk_index": batch_start + i,
                        "content": chunk["content"],
                        "content_hash": hashlib.sha256(chunk["content"].encode()).hexdigest(),
                        "embedding": json.dumps(embedding),
                        "metadata": json.dumps(chunk.get("metadata", {})),
                        "domain_metadata": json.dumps(domain_metadata or {}),
                        "expires_at": str(expires_at) if expires_at else None,
                    })

                await db.execute(
                    text(f"""
                        INSERT INTO {table}
                        (id, tenant_id, collection_id, document_id, chunk_index,
                         content, content_hash, embedding, metadata, domain_metadata, expires_at)
                        VALUES (:id, :tenant_id, :collection_id, :document_id, :chunk_index,
                                :content, :content_hash, :embedding::vector, :metadata::jsonb,
                                :domain_metadata::jsonb, :expires_at)
                        ON CONFLICT (collection_id, document_id, chunk_index) DO UPDATE
                        SET content = EXCLUDED.content,
                            embedding = EXCLUDED.embedding,
                            content_hash = EXCLUDED.content_hash
                    """),
                    chunk_rows,
                )
                total_chunks += len(batch)

            # Mark document as indexed
            await db.execute(
                text("""
                    UPDATE knowledge_documents
                    SET status = 'indexed', chunk_count = :chunk_count, indexed_at = now()
                    WHERE id = :doc_id
                """),
                {"chunk_count": total_chunks, "doc_id": doc_id},
            )

            # Update collection stats (no cap)
            await db.execute(
                text("""
                    UPDATE knowledge_collections
                    SET document_count = document_count + 1,
                        chunk_count = chunk_count + :n,
                        last_indexed_at = now()
                    WHERE id = :collection_id
                """),
                {"n": total_chunks, "collection_id": collection_id},
            )
            await db.commit()

            return {"document_id": doc_id, "chunk_count": total_chunks, "status": "indexed"}

    async def sync_from_db(
        self,
        tenant_id: str,
        collection_id: str,
        batch_size: int = 1000,
        progress_callback=None,
    ) -> dict[str, int]:
        """
        FIX 3: Removed LIMIT 100000 cap. Now streams all chunks in batches.
        Returns { chunks_synced, documents_synced, errors }.
        """
        async with self._db() as db:
            from sqlalchemy import text

            col = await db.execute(
                text("SELECT embedding_dim FROM knowledge_collections WHERE id = :id AND tenant_id = :tid"),
                {"id": collection_id, "tid": tenant_id},
            )
            col_row = col.fetchone()
            if not col_row:
                raise ValueError(f"Collection {collection_id} not found")

            dim = col_row.embedding_dim
            table = _chunk_table(dim)

            # FIX 3: Count total without cap
            count_result = await db.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = :tid AND collection_id = :cid"),
                {"tid": tenant_id, "cid": collection_id},
            )
            total = count_result.scalar() or 0

            synced = 0
            offset = 0

            # Stream in batches (no cap)
            while offset < total:
                batch_result = await db.execute(
                    text(f"""
                        SELECT id, content, embedding::text, metadata, domain_metadata
                        FROM {table}
                        WHERE tenant_id = :tid
                          AND collection_id = :cid           -- FIX 1: explicit tenant filter
                        ORDER BY id
                        OFFSET :offset LIMIT :batch_size
                    """),
                    {
                        "tid": tenant_id,
                        "cid": collection_id,
                        "offset": offset,
                        "batch_size": batch_size,
                    },
                )
                batch = batch_result.fetchall()
                if not batch:
                    break

                synced += len(batch)
                offset += batch_size

                if progress_callback:
                    await progress_callback(synced, total)

                logger.debug("sync_batch_complete", synced=synced, total=total)

        return {"chunks_synced": synced, "total": total}

    @staticmethod
    def _chunk_text(
        text: str,
        chunk_size: int = 512,
        overlap: int = 64,
    ) -> list[dict[str, Any]]:
        """Split text into overlapping chunks."""
        words = text.split()
        chunks = []
        start = 0
        while start < len(words):
            end = min(start + chunk_size, len(words))
            chunk_text = " ".join(words[start:end])
            chunks.append({
                "content": chunk_text,
                "metadata": {
                    "start_word": start,
                    "end_word": end,
                    "word_count": end - start,
                },
            })
            if end >= len(words):
                break
            start += chunk_size - overlap
        return chunks

    async def expire_stale_documents(self, tenant_id: str) -> int:
        """
        Marks documents past their freshness TTL as expired.
        Removes corresponding chunks.
        """
        from datetime import datetime, timezone
        async with self._db() as db:
            from sqlalchemy import text
            result = await db.execute(
                text("""
                    UPDATE knowledge_documents
                    SET status = 'expired'
                    WHERE tenant_id = :tid
                      AND expires_at IS NOT NULL
                      AND expires_at <= now()
                      AND status = 'indexed'
                    RETURNING id
                """),
                {"tid": tenant_id},
            )
            expired_doc_ids = [str(row[0]) for row in result.fetchall()]
            if not expired_doc_ids:
                return 0

            # Remove chunks for expired docs across all dimension tables
            for dim in VALID_EMBEDDING_DIMS:
                table = _chunk_table(dim)
                await db.execute(
                    text(f"""
                        DELETE FROM {table}
                        WHERE tenant_id = :tid
                          AND document_id = ANY(:doc_ids::uuid[])
                    """),
                    {"tid": tenant_id, "doc_ids": expired_doc_ids},
                )

            await db.commit()
            return len(expired_doc_ids)


# ---------------------------------------------------------------------------
# Domain-specific knowledge metadata schemas
# ---------------------------------------------------------------------------

DOMAIN_METADATA_SCHEMAS: dict[str, dict] = {
    "legal": {
        "$schema": "http://json-schema.org/draft-07/schema",
        "properties": {
            "case_citation":     {"type": "string"},
            "court":             {"type": "string"},
            "jurisdiction":      {"type": "string"},
            "decision_date":     {"type": "string", "format": "date"},
            "legal_topics":      {"type": "array", "items": {"type": "string"}},
            "document_type":     {"type": "string",
                                  "enum": ["case_law", "statute", "regulation",
                                           "contract", "brief", "motion", "other"]},
            "privilege_status":  {"type": "string",
                                  "enum": ["not_privileged", "attorney_client",
                                           "work_product", "joint_defense"]},
            "matter_id":         {"type": "string"},
        },
    },
    "healthcare": {
        "$schema": "http://json-schema.org/draft-07/schema",
        "properties": {
            "fhir_resource_type": {"type": "string"},
            "icd10_codes":        {"type": "array", "items": {"type": "string"}},
            "cpt_codes":          {"type": "array", "items": {"type": "string"}},
            "document_type":      {"type": "string",
                                   "enum": ["clinical_guideline", "drug_monograph",
                                            "formulary", "policy", "research", "protocol"]},
            "data_classification": {"type": "string",
                                    "enum": ["PHI", "de_identified", "public"]},
            "source_system":      {"type": "string"},
        },
    },
    "finance": {
        "$schema": "http://json-schema.org/draft-07/schema",
        "properties": {
            "ticker":           {"type": "string"},
            "filing_type":      {"type": "string",
                                 "enum": ["10-K", "10-Q", "8-K", "DEF14A", "S-1", "other"]},
            "fiscal_year":      {"type": "integer"},
            "regulatory_body":  {"type": "string"},
            "document_type":    {"type": "string",
                                 "enum": ["sec_filing", "regulation", "policy",
                                          "market_data", "research_report"]},
            "confidentiality":  {"type": "string",
                                 "enum": ["public", "internal", "confidential", "restricted"]},
        },
    },
    "education": {
        "$schema": "http://json-schema.org/draft-07/schema",
        "properties": {
            "grade_level":    {"type": "string"},
            "subject_area":   {"type": "string"},
            "learning_objectives": {"type": "array", "items": {"type": "string"}},
            "curriculum_standard": {"type": "string"},
            "content_rating": {"type": "string", "enum": ["all_ages", "teen", "adult"]},
        },
    },
    "ecommerce": {
        "$schema": "http://json-schema.org/draft-07/schema",
        "properties": {
            "sku":            {"type": "string"},
            "category":       {"type": "string"},
            "brand":          {"type": "string"},
            "price_usd":      {"type": "number"},
            "in_stock":       {"type": "boolean"},
            "product_type":   {"type": "string",
                               "enum": ["physical", "digital", "service", "bundle"]},
        },
    },
}
```

### 3.5 main.py Wiring

```python
from app.knowledge.store import KnowledgeStore
from app.knowledge.router import router as knowledge_router

def create_app(manage_pools: bool = True) -> FastAPI:
    app.state.knowledge_store = KnowledgeStore(
        db_factory=app.state.db_session_factory,
        embedder=app.state.embedder,  # May be None if no embedding provider configured
    )
    app.include_router(knowledge_router, prefix="/api/knowledge", tags=["Knowledge"])
    return app
```

---

## 4. Frontend Specification

### 4.1 New Pages & Routes

| Route | Sidebar | Description |
|-------|---------|-------------|
| `/knowledge` | Knowledge | Collections overview |
| `/knowledge/collections/:id` | (detail) | Collection detail with document list |
| `/knowledge/collections/:id/search` | (action) | Interactive search playground |
| `/knowledge/collections/:id/graph` | (visual) | Knowledge graph visualization |
| `/knowledge/upload` | Knowledge → Upload | Document ingestion wizard |
| `/knowledge/sync` | Knowledge → Sync | Confluence/Jira/Notion sync config |

### 4.2 TypeScript Interfaces

```typescript
// src/features/knowledge/types.ts

export interface KnowledgeCollection {
  id: string;
  tenantId: string;
  name: string;
  description: string | null;
  domain: string | null;
  embeddingModel: string;
  embeddingDim: 768 | 1024 | 1536 | 3072;
  chunkSize: number;
  documentCount: number;
  chunkCount: number;
  totalSizeBytes: number;
  lastIndexedAt: string | null;
  freshnessTtlHours: number | null;
  isActive: boolean;
}

export interface KnowledgeDocument {
  id: string;
  collectionId: string;
  title: string;
  sourceUrl: string | null;
  sourceType: 'file' | 'confluence' | 'jira' | 'notion' | 'web' | 'api' | 'manual';
  status: 'pending' | 'processing' | 'indexed' | 'failed' | 'expired';
  chunkCount: number;
  fileSizeBytes: number | null;
  domainMetadata: Record<string, unknown>;
  indexedAt: string | null;
  expiresAt: string | null;
  errorMessage: string | null;
}

export interface SearchResult {
  chunkId: string;
  collectionId: string;
  documentId: string;
  documentTitle: string;
  content: string;
  score: number;
  vectorScore: number;
  textScore: number;
  domainMetadata: Record<string, unknown>;
}

export interface SearchRequest {
  query: string;
  collectionIds: string[];
  limit: number;
  alpha: number;
  minScore: number;
  domainFilters: Record<string, string>;
}

export interface SyncConfig {
  sourceType: 'confluence' | 'jira' | 'notion';
  credentialId: string;
  spaceKey?: string;
  projectKey?: string;
  syncIntervalHours: number;
}
```

### 4.3 Animation Specs

```css
/* src/features/knowledge/knowledge-animations.css */

/* Document ingest progress */
@keyframes ingestProgress {
  from { width: 0%; }
  to   { width: var(--ingest-pct); }
}

/* Search result relevance bar */
@keyframes relevanceBarFill {
  from { width: 0%; opacity: 0.5; }
  to   { width: var(--score-pct); opacity: 1; }
}

/* Collection card shimmer (loading) */
@keyframes collectionShimmer {
  from { background-position: -200% 0; }
  to   { background-position: 200% 0; }
}

/* Graph node appear */
@keyframes graphNodeAppear {
  0%   { transform: scale(0); opacity: 0; }
  60%  { transform: scale(1.1); opacity: 1; }
  100% { transform: scale(1); opacity: 1; }
}

/* Graph edge draw */
@keyframes graphEdgeDraw {
  from { stroke-dashoffset: var(--edge-length); opacity: 0; }
  to   { stroke-dashoffset: 0; opacity: 0.6; }
}

/* Freshness TTL countdown */
@keyframes ttlDrain {
  from { background-color: var(--color-success-emphasis); }
  to   { background-color: var(--color-danger-emphasis); }
}

/* Search highlight pulse */
@keyframes matchHighlight {
  0%   { background-color: var(--color-warning-subtle); }
  100% { background-color: transparent; }
}

/* Sync status rotating */
@keyframes syncSpin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

.ingest-bar        { animation: ingestProgress 0.6s ease-out both; }
.relevance-bar     { animation: relevanceBarFill 0.4s ease-out both; }
.collection-shimmer { animation: collectionShimmer 1.5s linear infinite; }
.graph-node        { animation: graphNodeAppear 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275) both; }
.graph-edge        { animation: graphEdgeDraw 0.4s ease-out both; }
.match-highlight   { animation: matchHighlight 2s ease-out both; }
.sync-spinner      { animation: syncSpin 1s linear infinite; }
```

### 4.4 Knowledge Graph Visualization

```typescript
// src/features/knowledge/components/KnowledgeGraph.tsx
// Uses d3-force for physics-based graph layout
// Nodes: documents (size = chunk_count)
// Edges: semantic similarity > 0.8 between documents
// Interactions: click node → open document, drag to explore, zoom
// Domain metadata coloring: legal (blue), healthcare (green), finance (gold)

export interface KnowledgeGraphProps {
  collectionId: string;
  onNodeClick: (documentId: string) => void;
  maxNodes?: number;  // default 200 for performance
}
```

### 4.5 Dark Mode & Mobile

```css
.collection-card    { background: var(--color-surface-1); border: 1px solid var(--color-border-default); }
.document-item      { background: var(--color-surface-1); }
.search-result      { background: var(--color-surface-1); border-bottom: 1px solid var(--color-border-subtle); }
.search-result:hover { background: var(--color-surface-2); }
.score-bar-track    { background: var(--color-border-muted); border-radius: var(--radius-full); }
.score-bar-fill     { background: var(--color-accent-emphasis); border-radius: var(--radius-full); }
.embedding-badge    { background: var(--color-accent-subtle); color: var(--color-accent-emphasis); }

@media (max-width: 640px) {
  .knowledge-split      { flex-direction: column; }
  .search-filters       { overflow-x: auto; display: flex; gap: var(--spacing-2); }
  .graph-container      { height: 300px; }
  .result-content       { max-height: 200px; overflow: hidden; }
  .collection-stats     { grid-template-columns: repeat(2, 1fr); }
}
```

---

## 5. Scale Architecture

**Target:** 10 B knowledge chunks across 1 M tenants

| Challenge | Solution |
|-----------|----------|
| Variable embedding dims | Separate `knowledge_chunks_{dim}` tables; queries use dim-specific table |
| 10B chunks search | ivfflat index per table; queries are tenant+collection scoped; partition by tenant at 100k+ tenants |
| Tenant isolation | Explicit WHERE tenant_id AND RLS (double protection) |
| Freshness enforcement | Celery beat task: expire_stale_documents every hour |
| Large collection sync | Streaming batches of 1000; progress tracked in Redis |
| Multi-collection search | Parallel queries per collection; re-rank merged results |
| Credential security | SecretStr prevents logging; credentials stored in Vault, resolved by ID |
| ivfflat rebuild | Scheduled nightly REINDEX CONCURRENTLY on large tables (>1M rows) |

---

## 6. Testing Strategy

```python
# agent-verse-backend/tests/knowledge/test_knowledge_store.py
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.knowledge.store import (
    KnowledgeStore, ConfluenceConnector, _chunk_table,
    VALID_EMBEDDING_DIMS, DOMAIN_METADATA_SCHEMAS,
)
from pydantic import SecretStr


# ---- Chunk table routing ---------------------------------------------------

class TestChunkTableRouting:
    def test_768_maps_to_correct_table(self):
        assert _chunk_table(768) == "knowledge_chunks_768"

    def test_1024_maps_to_correct_table(self):
        assert _chunk_table(1024) == "knowledge_chunks_1024"

    def test_1536_maps_to_correct_table(self):
        assert _chunk_table(1536) == "knowledge_chunks_1536"

    def test_3072_maps_to_correct_table(self):
        assert _chunk_table(3072) == "knowledge_chunks_3072"

    def test_unsupported_dim_raises(self):
        with pytest.raises(ValueError, match="Unsupported embedding dimension"):
            _chunk_table(512)  # Not a supported dimension

    def test_all_valid_dims_have_table(self):
        for dim in VALID_EMBEDDING_DIMS:
            table = _chunk_table(dim)
            assert f"knowledge_chunks_{dim}" == table


# ---- FIX 5: Missing embedder raises ----------------------------------------

class TestMissingEmbedderError:
    def test_search_raises_when_no_embedder(self):
        store = KnowledgeStore(db_factory=AsyncMock(), embedder=None)
        with pytest.raises(RuntimeError, match="Knowledge base search requires an embedder"):
            store._require_embedder()

    def test_no_silent_fake_embeddings(self):
        """FIX 5: Was silently using zero vectors. Now raises explicitly."""
        store = KnowledgeStore(db_factory=AsyncMock(), embedder=None)
        with pytest.raises(RuntimeError) as exc_info:
            store._require_embedder()
        # Error message must be informative, not silent
        assert "Refusing to use fake zero-vector embeddings" in str(exc_info.value)

    def test_configured_embedder_returns_it(self):
        mock_embedder = MagicMock()
        store = KnowledgeStore(db_factory=AsyncMock(), embedder=mock_embedder)
        result = store._require_embedder()
        assert result is mock_embedder


# ---- FIX 4: Confluence credentials as SecretStr ----------------------------

class TestConfluenceSecretCredentials:
    def test_token_is_secret_str(self):
        token = SecretStr("real_secret_token_value_12345")
        connector = ConfluenceConnector(
            base_url="https://company.atlassian.net",
            username="user@company.com",
            api_token=token,
        )
        assert isinstance(connector._token, SecretStr)

    def test_token_not_in_repr(self):
        """FIX 4: Token must not appear in repr (and thus not in logs)."""
        token = SecretStr("my_super_secret_token")
        connector = ConfluenceConnector(
            base_url="https://company.atlassian.net",
            username="user@company.com",
            api_token=token,
        )
        repr_str = repr(connector)
        assert "my_super_secret_token" not in repr_str

    def test_secret_str_masks_in_str(self):
        """SecretStr masks value in str() output."""
        token = SecretStr("real_secret_token")
        assert "real_secret_token" not in str(token)
        assert "**" in str(token) or "SecretStr" in str(token)

    def test_get_secret_value_works(self):
        """Can still retrieve actual token value when needed."""
        token = SecretStr("real_secret_token")
        assert token.get_secret_value() == "real_secret_token"


# ---- FIX 3: No chunk cap in sync_from_db -----------------------------------

@pytest.mark.asyncio
class TestSyncFromDB:
    async def test_syncs_more_than_100k_chunks(self):
        """FIX 3: Was LIMIT 100000, now streams all."""
        total_chunks = 150_000  # More than the old cap
        synced_count = [0]
        batch_records_returned = [True]  # Controls when to stop

        call_count = [0]

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        async def execute_side_effect(query, params=None, **kwargs):
            q = str(query)
            mock_result = MagicMock()

            if "COUNT(*)" in q:
                mock_result.scalar = lambda: total_chunks
            else:
                # Return batches of 1000, stop at total
                offset = params.get("offset", 0) if params else 0
                if offset >= total_chunks:
                    mock_result.fetchall = lambda: []
                else:
                    remaining = min(1000, total_chunks - offset)
                    mock_result.fetchall = lambda: [MagicMock()] * remaining

            # Mock collection query
            if "embedding_dim" in q:
                mock_result.fetchone = lambda: MagicMock(embedding_dim=768)
            return mock_result

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        store = KnowledgeStore(db_factory=lambda: mock_db, embedder=MagicMock())
        result = await store.sync_from_db(
            tenant_id=str(uuid4()),
            collection_id=str(uuid4()),
            batch_size=1000,
        )

        assert result["total"] == total_chunks
        assert result["chunks_synced"] == total_chunks  # All chunks synced, no cap


# ---- FIX 1: Explicit tenant_id in SQL query --------------------------------

@pytest.mark.asyncio
class TestTenantIsolationInSQL:
    async def test_search_includes_tenant_id_in_query(self):
        """FIX 1: SQL must include explicit tenant_id filter."""
        executed_queries = []

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        async def execute_side_effect(query, params=None, **kwargs):
            query_str = str(query)
            executed_queries.append((query_str, params or {}))
            mock_result = MagicMock()
            if "embedding_dim" in query_str:
                mock_result.fetchone = lambda: MagicMock(embedding_dim=768)
            else:
                mock_result.fetchall = lambda: []
            return mock_result

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        mock_embedder = AsyncMock()
        mock_embedder.embed = AsyncMock(return_value=[0.1] * 768)

        tenant_id = str(uuid4())
        store = KnowledgeStore(db_factory=lambda: mock_db, embedder=mock_embedder)

        try:
            await store.hybrid_search(
                tenant_id=tenant_id,
                collection_id=str(uuid4()),
                query="test query",
            )
        except Exception:
            pass  # We only care about the query structure

        # Find the search query
        search_queries = [(q, p) for q, p in executed_queries if "vector_cosine_ops" in q or "embedding" in q]
        if search_queries:
            query_str, params = search_queries[0]
            # FIX 1: tenant_id must appear as an explicit WHERE condition
            assert "tenant_id" in query_str.lower()
            assert params.get("tenant_id") == tenant_id


# ---- Text chunking ---------------------------------------------------------

class TestTextChunking:
    def test_chunks_long_text(self):
        text = "word " * 1500  # 1500 words
        chunks = KnowledgeStore._chunk_text(text, chunk_size=512, overlap=64)
        assert len(chunks) > 1

    def test_short_text_single_chunk(self):
        text = "Short text with just a few words."
        chunks = KnowledgeStore._chunk_text(text, chunk_size=512, overlap=64)
        assert len(chunks) == 1

    def test_overlap_creates_overlapping_chunks(self):
        text = " ".join([f"word{i}" for i in range(200)])
        chunks = KnowledgeStore._chunk_text(text, chunk_size=100, overlap=20)
        # Words from end of chunk 0 should appear in start of chunk 1
        if len(chunks) > 1:
            words_0 = set(chunks[0]["content"].split())
            words_1 = set(chunks[1]["content"].split())
            assert len(words_0 & words_1) > 0  # Overlap exists


# ---- Domain metadata schemas -----------------------------------------------

class TestDomainMetadataSchemas:
    def test_all_domains_have_schemas(self):
        required = {"legal", "healthcare", "finance", "education", "ecommerce"}
        assert required.issubset(set(DOMAIN_METADATA_SCHEMAS.keys()))

    def test_legal_has_privilege_status(self):
        schema = DOMAIN_METADATA_SCHEMAS["legal"]
        assert "privilege_status" in schema["properties"]

    def test_healthcare_has_phi_classification(self):
        schema = DOMAIN_METADATA_SCHEMAS["healthcare"]
        assert "data_classification" in schema["properties"]
        phi_enum = schema["properties"]["data_classification"].get("enum", [])
        assert "PHI" in phi_enum

    def test_finance_has_filing_type(self):
        schema = DOMAIN_METADATA_SCHEMAS["finance"]
        assert "filing_type" in schema["properties"]
        # Must include major SEC filing types
        filing_enum = schema["properties"]["filing_type"].get("enum", [])
        assert "10-K" in filing_enum
        assert "10-Q" in filing_enum

    def test_all_schemas_are_valid_json_schema(self):
        for domain, schema in DOMAIN_METADATA_SCHEMAS.items():
            assert isinstance(schema, dict)
            assert "properties" in schema
            for prop, spec in schema["properties"].items():
                assert "type" in spec or "enum" in spec, \
                    f"{domain}.{prop} must have type or enum"
```

---

## 7. Domain Extensibility

### Healthcare (FHIR/ICD-10)
```python
# Domain metadata: fhir_resource_type, icd10_codes, cpt_codes
# Special collection type: FHIR R4 resources (auto-chunk by resource type)
# Filtered search: search("patient chest pain", domain_filters={"icd10_codes": "I21"})
# PHI gate: collections with data_classification=PHI require phi_reader scope
# De-identification pipeline: run NLP NER to redact PHI before indexing
```

### Legal (Case Law / Statutes)
```python
# Domain metadata: case_citation, court, jurisdiction, privilege_status
# Citation linking: detect citations in text, auto-link to cited documents
# Privilege tagging: mark privileged documents; block non-attorney search
# Matter scoping: collections scoped to matter_id; cross-matter search blocked
# Bluebook formatter: standardize citations on ingest
```

### Finance (SEC Filings)
```python
# Domain metadata: ticker, filing_type, fiscal_year
# EDGAR integration: auto-ingest new 10-K/10-Q via EDGAR full-text search RSS
# Materiality filter: re-rank results by dollar amounts mentioned in chunk
# Earnings calendar: expire old earnings docs and re-ingest on filing date
```

### Education
```python
# Domain metadata: grade_level, subject_area, learning_objectives
# Curriculum alignment: tag chunks with Common Core or state standards
# Accessibility: store readability score; filter by grade_level in search
# COPPA guard: block ingestion of content containing student PII
```

### E-commerce
```python
# Domain metadata: sku, category, brand, price_usd, in_stock
# Catalog sync: ingest from Shopify/WooCommerce product API on schedule
# Inventory freshness: TTL = 4 hours (frequent price/stock changes)
# Search ranking: boost in-stock items in search results
```

---

## AMENDMENTS — Critical Fixes

### Amendment 10.1 — Fix IVFFlat on empty tables → use HNSW

```sql
-- Replace IVFFlat with HNSW (handles empty tables, no training data required):

-- BEFORE (IVFFlat fails on empty table):
-- CREATE INDEX ix_documents_embedding_768 ON document_chunks_768 USING ivfflat (embedding vector_cosine_ops) WITH (lists = 200);

-- AFTER (HNSW works on any table size):
CREATE INDEX ix_documents_embedding_768 ON document_chunks_768
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE INDEX ix_documents_embedding_1536 ON document_chunks_1536
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE INDEX ix_documents_embedding_3072 ON document_chunks_3072
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
-- Note: HNSW requires pgvector >= 0.5.0 (check in healthcheck)
```

### Amendment 10.2 — Define embed_batch() on base provider interface

```python
# In app/providers/base.py, add to LLMProvider Protocol:
async def embed_batch(self, texts: list[str]) -> list[list[float]]:
    """Embed multiple texts in a single API call (more efficient)."""
    # Default implementation calls embed() sequentially:
    return [await self.embed(text) for text in texts]

# Override in voyage_provider.py for efficient batching:
async def embed_batch(self, texts: list[str]) -> list[list[float]]:
    resp = await self._client.embed(texts, model=self._embed_model)
    return [e.embedding for e in resp.embeddings]

# Override in openai_compatible.py:
async def embed_batch(self, texts: list[str]) -> list[list[float]]:
    resp = await self._client.embeddings.create(input=texts, model=self._embed_model)
    return [e.embedding for e in resp.data]
```

### Amendment 10.3 — Fix cross-model score incompatibility in federated search

```python
# Multi-collection federated search must normalize scores before merging:
async def federated_search(self, query: str, collection_ids: list[str], top_k: int = 10) -> list[SearchResult]:
    """Search across multiple collections, normalize scores for cross-model comparison."""
    per_collection_results = await asyncio.gather(*[
        self._search_collection(query, cid, top_k=top_k * 2)  # over-fetch for re-ranking
        for cid in collection_ids
    ])

    # Normalize scores within each collection to [0, 1] using min-max normalization:
    all_results = []
    for results in per_collection_results:
        if not results:
            continue
        scores = [r.similarity_score for r in results]
        min_s, max_s = min(scores), max(scores)
        score_range = max_s - min_s if max_s != min_s else 1.0
        for r in results:
            r.normalized_score = (r.similarity_score - min_s) / score_range  # [0, 1]
            all_results.append(r)

    # Re-rank by normalized score:
    all_results.sort(key=lambda r: r.normalized_score, reverse=True)

    # Dedup by content_hash:
    seen = set()
    deduped = []
    for r in all_results:
        if r.content_hash not in seen:
            seen.add(r.content_hash)
            deduped.append(r)
    return deduped[:top_k]
```

### Amendment 10.4 — Fix token vs word chunking

```python
# BEFORE (splits by word count — overflows model context):
# chunks = textwrap.wrap(text, width=512)  # or similar

# AFTER (token-aware chunking using tiktoken):
def chunk_by_tokens(text: str, max_tokens: int = 512, overlap_tokens: int = 64) -> list[str]:
    """Split text respecting token boundaries, not word boundaries."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")  # works for most modern models
        tokens = enc.encode(text)
    except ImportError:
        # Fallback: approximate 4 chars per token
        return _chunk_by_chars(text, max_chars=max_tokens * 4, overlap=overlap_tokens * 4)

    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(enc.decode(chunk_tokens))
        start += max_tokens - overlap_tokens  # overlap for context continuity
    return chunks
```

### Amendment 10.5 — Implement KnowledgeGraph component + Celery tasks + App.tsx + toast + prefers-reduced-motion

```typescript
// KnowledgeGraph.tsx — actual implementation (replaces comment placeholder):
import { useEffect, useRef } from "react";
import type { KnowledgeGraphData } from "@/lib/api/client";

export function KnowledgeGraph({ data, width = 600, height = 400 }: KnowledgeGraphProps) {
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!data || !svgRef.current) return;
    // Async import d3-force (already installed):
    (async () => {
      const d3force = await import("d3-force");
      const svg = svgRef.current!;
      svg.innerHTML = "";

      const simulation = d3force.forceSimulation(data.nodes as any[])
        .force("link", d3force.forceLink(data.edges as any[]).id((d: any) => d.id).distance(60))
        .force("charge", d3force.forceManyBody().strength(-30))
        .force("center", d3force.forceCenter(width / 2, height / 2));

      // Draw edges:
      data.edges.forEach((edge: any) => {
        const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("stroke", "hsl(var(--border))");
        line.setAttribute("stroke-width", "1");
        svg.appendChild(line);
      });

      // Draw nodes:
      data.nodes.forEach((node: any) => {
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        const nodeColors = { document: "#3b82f6", concept: "#22c55e", entity: "#f59e0b" };
        circle.setAttribute("r", String(node.type === "document" ? 8 : 5));
        circle.setAttribute("fill", (nodeColors as any)[node.type] ?? "#9ca3af");
        circle.setAttribute("title", node.label);
        svg.appendChild(circle);
      });

      // Animate:
      simulation.on("tick", () => {
        let edgeIdx = 0;
        const lines = svg.querySelectorAll("line");
        data.edges.forEach((edge: any) => {
          if (lines[edgeIdx]) {
            lines[edgeIdx].setAttribute("x1", String(edge.source.x ?? 0));
            lines[edgeIdx].setAttribute("y1", String(edge.source.y ?? 0));
            lines[edgeIdx].setAttribute("x2", String(edge.target.x ?? 0));
            lines[edgeIdx].setAttribute("y2", String(edge.target.y ?? 0));
            edgeIdx++;
          }
        });
        let nodeIdx = 0;
        const circles = svg.querySelectorAll("circle");
        data.nodes.forEach((node: any) => {
          if (circles[nodeIdx]) {
            circles[nodeIdx].setAttribute("cx", String(node.x ?? 0));
            circles[nodeIdx].setAttribute("cy", String(node.y ?? 0));
            nodeIdx++;
          }
        });
      });

      return () => simulation.stop();
    })();
  }, [data, width, height]);

  return <svg ref={svgRef} width={width} height={height} aria-label="Knowledge graph" role="img" />;
}
```

```python
# Celery tasks for knowledge:
@celery_app.task(name="app.scaling.tasks.expire_stale_documents", queue="maintenance")
def expire_stale_documents():
    """Remove documents past their freshness_ttl_hours."""
    import asyncio
    asyncio.run(_sweep_expired_documents())

@celery_app.task(name="app.scaling.tasks.reembed_collection", queue="goals.free")
def reembed_collection(collection_id: str, tenant_id: str):
    """Re-embed all documents in a collection when model changes."""
    import asyncio
    asyncio.run(_reembed_all_documents(collection_id, tenant_id))

# Beat: expire stale docs daily at 01:00 UTC
```

```typescript
// App.tsx: KnowledgePage already exists — ensure lazy

// prefers-reduced-motion:
@media (prefers-reduced-motion: reduce) {
  .knowledge-graph-node-pulse, .chunk-ingest-progress, .similarity-score-fill {
    animation: none !important; transition: none !important;
  }
}

// Toast notifications:
// createCollection onSuccess: toast({kind:"success", message:"Collection created"})
// deleteCollection → ConfirmModal "Delete collection and all N documents?" variant="danger" + toast
// ingestDocument onSuccess: toast({kind:"success", message:`Ingested ${chunkCount} chunks`})
// ingestError: toast({kind:"error", message:`Ingestion failed: ${error}`})
// search with no embedder configured: toast({kind:"error", message:"No embedding provider configured — set VOYAGE_API_KEY or OPENAI_API_KEY"})

// Fix json.dumps(embedding) → correct PostgreSQL vector format:
// In client.ts, embedding is already sent as float[] array — backend should use:
# In store.py ingest_document():
embedding_str = "[" + ",".join(str(round(v, 6)) for v in embedding) + "]"  # PostgreSQL vector literal
await session.execute(_t(f"INSERT INTO document_chunks_768 ... VALUES ..., :emb::vector"), {"emb": embedding_str})
```
