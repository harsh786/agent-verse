"""Knowledge Bases v2 — comprehensive test suite.

Covers all 8 required fixes:
  1. Explicit tenant_id filter in all SQL queries (defence-in-depth)
  2. embed_batch() defined on all provider implementations
  3. Token-aware chunking respects model context window
  4. Federated search normalises scores across collections
  5. embedder=None raises HTTP 503 (not silent empty vectors)
  6. Confluence/Jira tokens stored as SecretStr (not plain str)
  7. sync_from_db() uses streaming cursor — no 100K hard cap
  8. Migration 0062 creates HNSW indexes, not IVFFlat
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Fix 1: Explicit tenant_id in all SQL queries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_id_filter_explicit_in_all_queries() -> None:
    """hybrid_search_db() must include tenant_id in WHERE clause (Fix 1)."""
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import PlanTier, TenantContext

    tenant_id = str(uuid4())
    tenant_ctx = TenantContext(
        tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k1"
    )

    executed: list[tuple[str, dict]] = []

    mock_session = AsyncMock()

    async def _execute(query, params=None, **kwargs):  # type: ignore[no-untyped-def]
        q = str(query)
        executed.append((q, params or {}))
        result = MagicMock()
        # Return empty collection (no dimension found → falls back to 'documents')
        result.fetchone = lambda: None
        result.fetchall = lambda: []
        return result

    mock_session.execute = AsyncMock(side_effect=_execute)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    # Patch sqlalchemy_rls_context so we don't need a real DB session
    class _FakeRLS:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_: object) -> None:
            pass

    import app.db.rls as rls_mod
    original = getattr(rls_mod, "sqlalchemy_rls_context", None)
    rls_mod.sqlalchemy_rls_context = lambda *a, **kw: _FakeRLS()  # type: ignore[assignment]

    try:
        store = KnowledgeStore(db_session_factory=lambda: mock_session)
        await store.hybrid_search_db(
            query="test query",
            query_embedding=[0.1] * 768,
            collection_id=str(uuid4()),
            tenant_ctx=tenant_ctx,
            top_k=5,
        )
    except Exception:
        pass  # We only care about query structure, not result processing
    finally:
        if original is not None:
            rls_mod.sqlalchemy_rls_context = original

    # At least one query should be the hybrid search SQL
    all_queries = " ".join(q for q, _ in executed).lower()
    # FIX 1: tenant_id must appear in query text
    assert "tenant_id" in all_queries, (
        "hybrid_search_db() SQL must include explicit tenant_id filter. "
        "Relying on RLS alone is insufficient for defence-in-depth."
    )

    # And it must be passed as a parameter (not hardcoded)
    all_params: dict = {}
    for _, params in executed:
        all_params.update(params)

    assert "tid" in all_params or "tenant_id" in all_params, (
        "tenant_id must be passed as a bound parameter, not hardcoded in SQL."
    )


# ---------------------------------------------------------------------------
# Fix 2: embed_batch() defined on all providers
# ---------------------------------------------------------------------------


def test_embed_batch_defined_on_all_providers() -> None:
    """Every provider must expose embed_batch() (Fix 2)."""
    from app.providers.fake import FakeProvider
    from app.providers.openai_compatible import OpenAICompatibleProvider
    from app.providers.voyage_provider import LocalEmbedProvider, VoyageProvider

    for cls in (FakeProvider, OpenAICompatibleProvider, VoyageProvider, LocalEmbedProvider):
        assert hasattr(cls, "embed_batch"), (
            f"{cls.__name__} is missing embed_batch(). "
            "Add it to support efficient batch embedding."
        )
        assert callable(getattr(cls, "embed_batch")), (
            f"{cls.__name__}.embed_batch must be callable."
        )


@pytest.mark.asyncio
async def test_fake_provider_embed_batch_returns_correct_shape() -> None:
    """FakeProvider.embed_batch() returns one vector per input text."""
    from app.providers.fake import FakeProvider

    provider = FakeProvider(embed_dim=768)
    texts = ["hello world", "foo bar", "baz"]
    embeddings = await provider.embed_batch(texts)

    assert len(embeddings) == len(texts), "Must return one embedding per text"
    for emb in embeddings:
        assert len(emb) == 768, "Each embedding must have the declared dimension"


# ---------------------------------------------------------------------------
# Fix 3 (spec Fix 6): Token-aware chunking (chunker_v2)
# ---------------------------------------------------------------------------


def test_token_aware_chunking_respects_model_context() -> None:
    """chunk_by_tokens() must produce chunks ≤ max_tokens (Fix 6 / chunk spec)."""
    from app.knowledge.chunker_v2 import chunk_by_tokens

    long_text = " ".join(f"token{i}" for i in range(2000))
    chunks = chunk_by_tokens(long_text, max_tokens=256, overlap_tokens=32)

    assert len(chunks) > 1, "Long text must be split into multiple chunks"
    assert all(len(c) > 0 for c in chunks), "No empty chunks allowed"

    # Attempt to verify token count when tiktoken is available
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        for chunk in chunks:
            token_count = len(enc.encode(chunk))
            assert token_count <= 256, (
                f"Chunk exceeds max_tokens=256 (got {token_count} tokens): {chunk[:80]!r}"
            )
    except ImportError:
        # tiktoken not installed in this environment — char-based fallback used
        for chunk in chunks:
            assert len(chunk) <= 256 * 4 + 100, (
                "Char-fallback chunk too large"
            )


def test_token_aware_chunking_overlap_creates_shared_content() -> None:
    """Adjacent chunks must share overlap_tokens worth of content."""
    from app.knowledge.chunker_v2 import chunk_by_tokens

    words = [f"word{i}" for i in range(300)]
    text = " ".join(words)
    chunks = chunk_by_tokens(text, max_tokens=100, overlap_tokens=20)

    if len(chunks) >= 2:
        # Content from the end of chunk[0] should appear in chunk[1]
        words_0 = set(chunks[0].split())
        words_1 = set(chunks[1].split())
        shared = words_0 & words_1
        assert len(shared) > 0, (
            "Adjacent chunks must share tokens (overlap_tokens=20 means "
            "the last 20 tokens of chunk N appear at the start of chunk N+1)"
        )


def test_token_aware_chunking_empty_input() -> None:
    """Empty / blank input must return empty list."""
    from app.knowledge.chunker_v2 import chunk_by_tokens

    assert chunk_by_tokens("") == []
    assert chunk_by_tokens("   \n  ") == []


def test_token_aware_chunking_short_text_single_chunk() -> None:
    """Text shorter than max_tokens must produce exactly one chunk."""
    from app.knowledge.chunker_v2 import chunk_by_tokens

    text = "Hello, world. This is a short document."
    chunks = chunk_by_tokens(text, max_tokens=512)
    assert len(chunks) == 1


# ---------------------------------------------------------------------------
# Fix 4: Federated search normalises scores
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_federated_search_normalizes_scores() -> None:
    """federated_search() normalises per-collection scores to [0, 1] (Fix 4)."""
    from app.knowledge.federated_search import federated_search

    cid_a = str(uuid4())
    cid_b = str(uuid4())

    # Two collections return results with very different score scales
    async def mock_search(query: str, cid: str, top_k: int) -> list[dict]:
        if cid == cid_a:
            # High-range scores (e.g. from a cosine similarity model ~0.9)
            return [
                {"content": f"doc_a_{i}", "score": 0.9 - i * 0.05, "content_hash": f"h_a_{i}"}
                for i in range(3)
            ]
        else:
            # Low-range scores (e.g. from a BM25 model ~0.1)
            return [
                {"content": f"doc_b_{i}", "score": 0.1 + i * 0.02, "content_hash": f"h_b_{i}"}
                for i in range(3)
            ]

    mock_store = MagicMock()
    mock_store.search = mock_search

    results = await federated_search(
        query="test query",
        collection_ids=[cid_a, cid_b],
        store=mock_store,
        top_k=6,
    )

    assert len(results) > 0, "Should return results"

    # After normalisation, normalized_score must be in [0, 1]
    for r in results:
        assert "normalized_score" in r, "Each result must have normalized_score"
        assert 0.0 <= r["normalized_score"] <= 1.0, (
            f"normalized_score out of [0,1]: {r['normalized_score']}"
        )


@pytest.mark.asyncio
async def test_federated_search_deduplicates_results() -> None:
    """Duplicate content across collections must appear only once."""
    from app.knowledge.federated_search import federated_search

    cid_a = str(uuid4())
    cid_b = str(uuid4())
    shared_hash = "shared_content_hash_123"

    async def mock_search(query: str, cid: str, top_k: int) -> list[dict]:
        return [{"content": "identical content", "score": 0.9, "content_hash": shared_hash}]

    mock_store = MagicMock()
    mock_store.search = mock_search

    results = await federated_search(
        query="test",
        collection_ids=[cid_a, cid_b],
        store=mock_store,
        top_k=10,
    )

    # The same content_hash must appear exactly once
    seen_hashes = [r["content_hash"] for r in results]
    assert len(seen_hashes) == len(set(seen_hashes)), (
        "Duplicate content_hash found in federated search results — dedup failed"
    )


@pytest.mark.asyncio
async def test_federated_search_handles_collection_error() -> None:
    """A failing collection must not abort the entire search."""
    from app.knowledge.federated_search import federated_search

    cid_good = str(uuid4())
    cid_bad = str(uuid4())

    async def mock_search(query: str, cid: str, top_k: int) -> list[dict]:
        if cid == cid_bad:
            raise RuntimeError("Collection unavailable")
        return [{"content": "good result", "score": 0.8, "content_hash": "good_hash"}]

    mock_store = MagicMock()
    mock_store.search = mock_search

    results = await federated_search(
        query="test",
        collection_ids=[cid_good, cid_bad],
        store=mock_store,
        top_k=10,
    )

    # Still returns results from the working collection
    assert len(results) == 1
    assert results[0]["content"] == "good result"


# ---------------------------------------------------------------------------
# Fix 5: embedder=None raises HTTP 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedder_none_raises_503() -> None:
    """search_knowledge() must raise HTTP 503 when embedder is None (Fix 5)."""
    from fastapi import HTTPException

    from app.api.knowledge import search_knowledge
    from app.rag.semantic_cache import SemanticCache
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import PlanTier, TenantContext

    tenant_ctx = TenantContext(
        tenant_id="tid-test", plan=PlanTier.PROFESSIONAL, api_key_id="k1"
    )

    mock_request = MagicMock()
    mock_request.state.tenant = tenant_ctx
    mock_request.app.state.knowledge_store = KnowledgeStore()
    mock_request.app.state.semantic_cache = SemanticCache()
    mock_request.app.state.embedder = None  # KEY: no embedder configured

    with pytest.raises(HTTPException) as exc_info:
        await search_knowledge(
            mock_request,
            q="breach of contract",
            collection_id=str(uuid4()),
        )

    assert exc_info.value.status_code == 503, (
        f"Expected 503 when embedder=None, got {exc_info.value.status_code}"
    )
    assert "embedding" in exc_info.value.detail.lower(), (
        "503 detail must mention embedding configuration"
    )


# ---------------------------------------------------------------------------
# Fix 6: Confluence/Jira/Slack tokens are SecretStr
# ---------------------------------------------------------------------------


def test_confluence_token_is_secret_str() -> None:
    """ConfluenceIngestRequest.token must be SecretStr, not plain str (Fix 6)."""
    from app.api.knowledge import ConfluenceIngestRequest

    # Instantiate the model with a token
    req = ConfluenceIngestRequest(
        collection_id=str(uuid4()),
        base_url="https://company.atlassian.net",
        space_key="LEGAL",
        token="my_real_secret_token_abc123",  # type: ignore[arg-type]
        user="user@company.com",
    )

    assert isinstance(req.token, SecretStr), (
        f"token must be SecretStr, got {type(req.token).__name__}"
    )


def test_confluence_token_not_in_str_repr() -> None:
    """SecretStr must mask the token value in str()/repr() output."""
    from app.api.knowledge import ConfluenceIngestRequest

    secret_value = "super_secret_token_xyz789"
    req = ConfluenceIngestRequest(
        collection_id=str(uuid4()),
        base_url="https://company.atlassian.net",
        space_key="ENG",
        token=secret_value,  # type: ignore[arg-type]
        user="dev@company.com",
    )

    serialized = str(req)
    assert secret_value not in serialized, (
        "Token value must not appear in string representation of the request model"
    )


def test_jira_token_is_secret_str() -> None:
    """JiraIngestRequest.token must be SecretStr (Fix 6)."""
    from app.api.knowledge import JiraIngestRequest

    req = JiraIngestRequest(
        collection_id=str(uuid4()),
        base_url="https://company.atlassian.net",
        project_key="PROJ",
        token="jira_secret_token_999",  # type: ignore[arg-type]
        user="user@company.com",
    )

    assert isinstance(req.token, SecretStr), (
        f"JiraIngestRequest.token must be SecretStr, got {type(req.token).__name__}"
    )


def test_slack_token_is_secret_str() -> None:
    """SlackIngestRequest.token must be SecretStr (Fix 6)."""
    from app.api.knowledge import SlackIngestRequest

    req = SlackIngestRequest(
        collection_id=str(uuid4()),
        channel_id="C0123456789",
        token="xoxb-slack-bot-token-secret",  # type: ignore[arg-type]
    )

    assert isinstance(req.token, SecretStr), (
        f"SlackIngestRequest.token must be SecretStr, got {type(req.token).__name__}"
    )


def test_secret_str_get_secret_value_works() -> None:
    """get_secret_value() must return the original token for actual API calls."""
    from app.api.knowledge import ConfluenceIngestRequest

    real_token = "actual_api_token_for_confluence_12345"
    req = ConfluenceIngestRequest(
        collection_id=str(uuid4()),
        base_url="https://example.atlassian.net",
        space_key="KB",
        token=real_token,  # type: ignore[arg-type]
        user="admin@example.com",
    )

    assert req.token.get_secret_value() == real_token, (
        "get_secret_value() must return the actual token string"
    )


# ---------------------------------------------------------------------------
# Fix 7: sync_from_db() uses streaming cursor — no hard cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_cursor_no_hard_cap() -> None:
    """sync_from_db() must stream all chunks without a LIMIT 100000 (Fix 7)."""
    import inspect

    from app.rag.store import KnowledgeStore

    source = inspect.getsource(KnowledgeStore.sync_from_db)

    # The old hard cap must be gone
    assert "100_000" not in source, (
        "sync_from_db() still contains LIMIT 100_000 hard cap. "
        "Remove it and replace with streaming cursor batches."
    )
    assert "100000" not in source, (
        "sync_from_db() still contains LIMIT 100000 hard cap."
    )

    # Streaming indicators must be present
    assert "offset" in source.lower() or "batch" in source.lower(), (
        "sync_from_db() must use offset-based streaming pagination."
    )


@pytest.mark.asyncio
async def test_sync_from_db_no_db_returns_zero() -> None:
    """sync_from_db() with no DB factory must return 0 immediately."""
    from app.rag.store import KnowledgeStore

    store = KnowledgeStore()  # no db_session_factory
    result = await store.sync_from_db()
    assert result == 0


# ---------------------------------------------------------------------------
# Fix 8: Migration 0062 creates HNSW indexes, not IVFFlat
# ---------------------------------------------------------------------------


def test_hnsw_index_created_not_ivfflat() -> None:
    """Migration 0062 must use HNSW indexes, not IVFFlat (Amendment 10.1)."""
    import pathlib

    migration_path = (
        pathlib.Path(__file__).parents[2]
        / "app/db/migrations/versions/0062_knowledge_v2.py"
    )

    assert migration_path.exists(), (
        f"Migration file not found: {migration_path}. "
        "Create app/db/migrations/versions/0062_knowledge_v2.py."
    )

    content = migration_path.read_text()

    # Must use HNSW
    assert "hnsw" in content.lower(), (
        "Migration 0062 must create HNSW indexes. "
        "IVFFlat requires training data and fails on empty tables."
    )

    # Must NOT use IVFFlat
    assert "ivfflat" not in content.lower(), (
        "Migration 0062 must not use IVFFlat. "
        "IVFFlat requires at least lists*16 rows before index build succeeds — "
        "it fails on empty tables. Use HNSW instead."
    )

    # Must include HNSW parameters
    assert "ef_construction" in content, (
        "HNSW index must specify ef_construction parameter."
    )


def test_migration_0062_chains_from_previous() -> None:
    """Migration 0062 must have a valid down_revision."""
    import pathlib
    import re

    migration_path = (
        pathlib.Path(__file__).parents[2]
        / "app/db/migrations/versions/0062_knowledge_v2.py"
    )

    content = migration_path.read_text()

    # down_revision must be set (not None)
    match = re.search(r'down_revision\s*=\s*["\'](\w+)["\']', content)
    assert match is not None, (
        "Migration 0062 must have a non-None down_revision to chain correctly."
    )


def test_migration_0062_creates_all_dimension_tables() -> None:
    """Migration 0062 must create chunk tables for all 4 supported dimensions."""
    import pathlib

    migration_path = (
        pathlib.Path(__file__).parents[2]
        / "app/db/migrations/versions/0062_knowledge_v2.py"
    )

    content = migration_path.read_text()

    for dim in [768, 1024, 1536, 3072]:
        assert f"knowledge_chunks_{dim}" in content or str(dim) in content, (
            f"Migration 0062 must create table for embedding dimension {dim}."
        )


def test_migration_0062_has_knowledge_collections_table() -> None:
    """Migration 0062 must create the knowledge_collections table."""
    import pathlib

    migration_path = (
        pathlib.Path(__file__).parents[2]
        / "app/db/migrations/versions/0062_knowledge_v2.py"
    )

    content = migration_path.read_text()
    assert "knowledge_collections" in content, (
        "Migration 0062 must create the knowledge_collections table."
    )
    assert "embedding_dim" in content, (
        "knowledge_collections must have an embedding_dim column."
    )
