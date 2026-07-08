"""All retrieval strategies: BM25, hybrid, vector, HyDE, multi-hop, fusion, colbert, corrective, adaptive."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession
from app.rag.store import KnowledgeStore
from app.rag.models import KnowledgeCollection, Chunk
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def populated_store(tenant_ctx: TenantContext) -> KnowledgeStore:
    store = KnowledgeStore()
    col = KnowledgeCollection(name="docs", collection_id="col1", embedder="fake")
    store.create_collection(col, tenant_ctx=tenant_ctx)
    chunks_data = [
        ("c1", "Python machine learning tutorial with scikit-learn", [0.9, 0.1] * 5),
        ("c2", "Java enterprise application development guide", [0.1, 0.9] * 5),
        ("c3", "Python deep learning with TensorFlow and Keras", [0.85, 0.15] * 5),
        ("c4", "JavaScript React frontend development", [0.2, 0.8] * 5),
        ("c5", "SQL database query optimization techniques", [0.5, 0.5] * 5),
    ]
    for chunk_id, content, embedding in chunks_data:
        store.ingest_chunk(
            Chunk(
                document_id=f"d_{chunk_id}",
                content=content,
                embedding=embedding,
                chunk_index=0,
                chunk_id=chunk_id,
                metadata={"source_url": f"https://example.com/{chunk_id}"},
            ),
            collection_id="col1",
            tenant_ctx=tenant_ctx,
        )
    return store


# ── VECTOR / COSINE SIMILARITY ────────────────────────────────────────────────

def test_vector_search_by_embedding(populated_store: KnowledgeStore, tenant_ctx: TenantContext) -> None:
    """Cosine similarity must rank Python ML chunks above Java/JS for Python query."""
    results = populated_store.hybrid_search(
        query="python machine learning",
        query_embedding=[0.9, 0.1] * 5,
        collection_id="col1",
        tenant_ctx=tenant_ctx,
        top_k=3,
    )
    assert len(results) >= 1
    top_ids = [r.chunk_id for r in results[:2]]
    assert "c1" in top_ids or "c3" in top_ids


# ── TRUE BM25 ─────────────────────────────────────────────────────────────────

def test_bm25_okapi_ranks_relevant_docs() -> None:
    from app.rag.bm25 import BM25Retriever

    retriever = BM25Retriever()
    retriever.index([
        {"chunk_id": "c1", "content": "Python machine learning tutorial scikit-learn"},
        {"chunk_id": "c2", "content": "Java enterprise application server deployment"},
        {"chunk_id": "c3", "content": "Python deep learning neural networks tensorflow"},
    ])
    results = retriever.search("Python machine learning", top_k=3)
    assert len(results) >= 1
    top_ids = [r.chunk_id for r in results[:2]]
    assert "c1" in top_ids or "c3" in top_ids
    # Java should not be #1
    assert results[0].chunk_id != "c2"


def test_bm25_idf_weights_rare_terms() -> None:
    from app.rag.bm25 import BM25Retriever

    retriever = BM25Retriever()
    retriever.index([
        {"chunk_id": "c1", "content": "agentverse dynamic orchestration system"},
        {"chunk_id": "c2", "content": "generic software system design"},
        {"chunk_id": "c3", "content": "agentverse goal execution engine"},
    ])
    results = retriever.search("agentverse", top_k=3)
    # "agentverse" only in c1, c3 — should rank higher than c2
    top_ids = [r.chunk_id for r in results]
    assert len(top_ids) >= 1
    assert "c2" not in top_ids[:2] or (len(top_ids) > 0 and top_ids[0] != "c2")


# ── HYBRID SEARCH (vector + FTS + trigram + BM25) ────────────────────────────

def test_hybrid_search_combines_signals(populated_store: KnowledgeStore, tenant_ctx: TenantContext) -> None:
    results = populated_store.hybrid_search(
        query="python ml tutorial",
        query_embedding=[0.9, 0.1] * 5,
        collection_id="col1",
        tenant_ctx=tenant_ctx,
        top_k=5,
    )
    assert len(results) >= 1
    # Must return results with required fields
    for r in results:
        assert hasattr(r, "chunk_id")
        assert hasattr(r, "content")
        assert hasattr(r, "score")
        assert r.score >= 0


def test_hybrid_search_with_metadata_filter(populated_store: KnowledgeStore, tenant_ctx: TenantContext) -> None:
    """Metadata filter must restrict results."""
    results = populated_store.hybrid_search(
        query="content",
        query_embedding=[0.5] * 10,
        collection_id="col1",
        tenant_ctx=tenant_ctx,
        top_k=10,
        metadata_filter={"source_url": "https://example.com/c1"},
    )
    assert len(results) == 1
    assert results[0].chunk_id == "c1"


# ── HYDE RETRIEVAL ────────────────────────────────────────────────────────────

async def test_hyde_generates_hypothetical_doc() -> None:
    from app.rag.engine import retrieve_hyde, RetrievalResult
    from app.providers.fake import FakeProvider

    session = AsyncMock(spec=AsyncSession)

    # Accept positional session arg + keyword args matching engine.hybrid_search signature
    async def fake_hybrid(session, **kw):  # noqa: ANN001
        return [RetrievalResult("c1", "Python ML content", 0.9, {}, ["vector"])]

    provider = FakeProvider(
        responses=["A machine learning tutorial using Python and scikit-learn for classification tasks."]
    )

    with patch("app.rag.engine.hybrid_search", side_effect=fake_hybrid):
        results = await retrieve_hyde(
            session,
            query="machine learning python",
            query_embedding=[0.1] * 10,
            collection_id="col1",
            provider=provider,
            top_k=5,
        )
    assert isinstance(results, list)


# ── MULTI-HOP RETRIEVAL ───────────────────────────────────────────────────────

async def test_multi_hop_decomposes_query() -> None:
    from app.rag.engine import retrieve_multi_hop, RetrievalResult
    from app.providers.fake import FakeProvider

    session = AsyncMock(spec=AsyncSession)

    call_count: list[int] = []

    async def fake_hybrid(session, **kw):  # noqa: ANN001
        call_count.append(1)
        return [RetrievalResult(f"c{len(call_count)}", "content", 0.8, {}, ["vector"])]

    # JSON list for sub-query decomposition
    provider = FakeProvider(responses=['["What is Python?", "What is machine learning?"]'])

    with patch("app.rag.engine.hybrid_search", side_effect=fake_hybrid):
        results = await retrieve_multi_hop(
            session,
            query="how does Python help with machine learning",
            query_embedding=[0.1] * 10,
            collection_id="col1",
            provider=provider,
            top_k=5,
        )
    assert len(call_count) >= 1  # At least one sub-query was issued
    assert isinstance(results, list)


# ── PARENT-CHILD RETRIEVAL ────────────────────────────────────────────────────

def test_parent_child_chunker_creates_hierarchy() -> None:
    from app.rag.parent_child_chunker import ParentChildChunker

    chunker = ParentChildChunker(parent_chunk_size=300, child_chunk_size=100, child_overlap=20)
    text = "Machine learning is powerful. " * 20
    parents = chunker.chunk(text, document_id="doc1")
    assert len(parents) >= 1
    for parent in parents:
        assert len(parent.children) >= 1
        for child in parent.children:
            assert child.parent_chunk_id == parent.chunk_id
            assert len(child.content) <= len(parent.content) + 10


# ── SENTENCE WINDOW RETRIEVAL ─────────────────────────────────────────────────

def test_sentence_window_chunker() -> None:
    from app.rag.sentence_window import SentenceWindowChunker

    chunker = SentenceWindowChunker(window_size=2)
    text = "First sentence. Second sentence. Third sentence. Fourth sentence."
    chunks = chunker.chunk(text)
    assert len(chunks) >= 3
    # Middle chunk should have larger window than just the sentence
    if len(chunks) >= 3:
        middle = chunks[1]
        assert len(middle["metadata"]["window_context"]) >= len(middle["content"])


def test_sentence_window_retriever_expands() -> None:
    from app.rag.sentence_window import SentenceWindowRetriever
    from app.rag.engine import RetrievalResult

    retriever = SentenceWindowRetriever()
    result = RetrievalResult(
        chunk_id="c1",
        content="Short sentence.",
        score=0.9,
        source_metadata={"window_context": "Previous. Short sentence. Next sentence."},
        retrieval_legs=["vector"],
    )
    expanded = retriever.expand([result])
    assert len(expanded[0].content) > len(result.content)


# ── MMR (TRUE MAXIMAL MARGINAL RELEVANCE) ────────────────────────────────────

def test_mmr_diversity_reranking() -> None:
    from app.context.rerank_policy import RerankPolicy

    policy = RerankPolicy()
    chunks = [
        {"chunk_id": "c1", "content": "Python machine learning tutorial", "score": 0.9},
        {"chunk_id": "c2", "content": "Python machine learning guide", "score": 0.85},
        {"chunk_id": "c3", "content": "Java enterprise development", "score": 0.4},
    ]
    result = policy._diversity_rerank(chunks)
    # c1 selected first (highest relevance score)
    assert result[0]["chunk_id"] == "c1"
    # c3 should appear — it's the diverse chunk
    ids = [c["chunk_id"] for c in result]
    assert "c3" in ids


def test_mmr_with_real_embeddings() -> None:
    from app.context.rerank_policy import RerankPolicy

    policy = RerankPolicy()
    chunks = [
        {"chunk_id": "c1", "content": "a", "score": 0.9, "embedding": [1.0, 0.0]},
        {"chunk_id": "c2", "content": "b", "score": 0.85, "embedding": [0.99, 0.01]},
        {"chunk_id": "c3", "content": "c", "score": 0.5, "embedding": [0.0, 1.0]},
    ]
    query_emb = [1.0, 0.0]
    result = policy._diversity_rerank(chunks, query_embedding=query_emb)
    # c1 first (most relevant to query)
    assert result[0]["chunk_id"] == "c1"
    # c3 or c2 second (depends on MMR tie-break)
    assert result[1]["chunk_id"] in ("c3", "c2")


# ── CROSS-ENCODER RERANKING ───────────────────────────────────────────────────

def test_cross_encoder_reranks() -> None:
    from app.context.rerank_policy import RerankPolicy

    policy = RerankPolicy()
    chunks = [
        {"chunk_id": "c1", "content": "Python is a machine learning language", "score": 0.3},
        {"chunk_id": "c2", "content": "Java is used for enterprise web services", "score": 0.8},
    ]
    result = policy._cross_encoder_rerank(chunks, "python machine learning")
    # Python chunk should rank higher for a python ML query
    assert result[0]["chunk_id"] == "c1"


# ── COLBERT LATE INTERACTION ──────────────────────────────────────────────────

def test_colbert_maxsim_scores() -> None:
    from app.rag.agentic.patterns.colbert import ColBERTPattern

    pattern = ColBERTPattern()
    score = pattern._maxsim_score(
        "python machine learning",
        "Python is great for machine learning and data science",
    )
    assert score > 0
    # Should score higher than irrelevant doc
    score2 = pattern._maxsim_score(
        "python machine learning",
        "The weather today is sunny and warm",
    )
    assert score > score2


# ── MEMORY STRATEGY RETRIEVAL ─────────────────────────────────────────────────

async def test_memory_strategy_returns_ltm() -> None:
    from app.rag.engine import retrieve
    from unittest.mock import AsyncMock, MagicMock

    session = AsyncMock(spec=AsyncSession)
    mock_ltm = AsyncMock()
    mock_mem = MagicMock()
    mock_mem.memory_id = "m1"
    mock_mem.content = "Always use OAuth 2.0 for Jira API authentication"
    mock_mem.confidence = 0.9
    mock_mem.memory_type = "procedure"
    mock_mem.source_goal_id = "g_past"
    mock_ltm.recall_async = AsyncMock(return_value=[mock_mem])

    mock_ctx = MagicMock()
    mock_ctx.tenant_id = "t1"

    results = await retrieve(
        session,
        query="Jira authentication",
        query_embedding=[0.1] * 10,
        collection_id="col1",
        strategy="memory",
        long_term_memory=mock_ltm,
        tenant_ctx=mock_ctx,
    )
    assert len(results) == 1
    assert results[0].source_metadata["source"] == "long_term_memory"
    assert "OAuth" in results[0].content
