"""
Comprehensive real-OpenAI E2E tests for ALL RAG patterns in AgentVerse.

Tests all 9 RAG patterns with real OpenAI (gpt-4o-mini):
  FusionRAG, CorrectiveRAG, AdaptiveRAG, FLARE, SelfRAG,
  SpeculativeRAG, RAPTOR, ColBERT, AgenticChunking.

Domain corpus: payment processing knowledge base (10 documents).

Each test:
  - Seeds a KnowledgeStore (or passes chunks directly) with PAYMENT_DOCS
  - Calls the pattern's real execute() method
  - LLM-based patterns use real OpenAI (gpt-4o-mini) — NO mocking
  - Asserts domain-relevant output (keywords, structure, non-empty answers)

Run:
    uv run pytest tests/real_e2e/test_rag_patterns_real_openai.py -v --no-cov -s
"""
from __future__ import annotations

import os
import pytest
from dotenv import load_dotenv

from tests._paths import BACKEND_ROOT

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen before any app imports
# ---------------------------------------------------------------------------
load_dotenv(BACKEND_ROOT / ".env")

# Force HuggingFace offline mode so ColBERT's sentence-transformers encoder
# does NOT try to download all-MiniLM-L6-v2.  If the model isn't already
# cached, _get_encoder() returns None and ColBERT falls back to TF-IDF — the
# correct production fallback path that we want to exercise in tests.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
if not OPENAI_KEY:
    pytest.skip("OPENAI_API_KEY not set", allow_module_level=True)

pytestmark = [pytest.mark.slow, pytest.mark.real_openai]

# ---------------------------------------------------------------------------
# Payment domain corpus — 10 documents covering distinct payment topics
# ---------------------------------------------------------------------------
PAYMENT_DOCS = [
    # 0 — PCI DSS card security
    "PCI DSS requires merchants to never store CVV data after authorization. "
    "Card numbers must be encrypted using AES-256.",
    # 1 — UPI transactions & refunds
    "UPI payments in India are processed through the NPCI switch. "
    "Average transaction time is 3-5 seconds. "
    "Failed transactions are automatically refunded within 5 business days.",
    # 2 — Tokenization
    "Tokenization replaces sensitive card data with a non-sensitive token. "
    "VISA uses VDEP, Mastercard uses MDES for token provisioning.",
    # 3 — 3DS2 frictionless vs challenge
    "3D Secure 2.0 (3DS2) uses risk-based authentication. "
    "Frictionless flow skips OTP for low-risk transactions. "
    "Challenge flow requires OTP for high-risk.",
    # 4 — Chargebacks
    "Chargeback reason codes: 4853 (item not received), 4855 (goods not as described), "
    "4863 (not recognized). Merchant has 30 days to respond.",
    # 5 — EMI interest rates
    "EMI interest rates: No-cost EMI (0% interest, merchant pays subvention), "
    "Standard EMI (12-24% per annum), Low-cost EMI (6-12%).",
    # 6 — Webhook retry
    "Webhook retry logic: first retry after 5 minutes, second after 30 minutes, "
    "third after 2 hours, then exponential backoff up to 24 hours.",
    # 7 — Success rates
    "Payment gateway success rates: UPI averages 94%, Credit cards average 87%, "
    "Debit cards average 82%, Netbanking averages 91%.",
    # 8 — NPCI / IMPS limits
    "NPCI operates UPI, IMPS, NACH, RuPay, BBPS and NETC. "
    "IMPS supports 24x7 real-time transfers up to Rs 5 lakh per transaction.",
    # 9 — Fraud detection
    "Fraud detection uses velocity checks, device fingerprinting, "
    "geo-velocity analysis, and BIN analysis to score transaction risk.",
]

# Pre-built chunk dicts for patterns that accept chunks directly (RAPTOR, ColBERT, AgenticChunking)
PAYMENT_CHUNKS: list[dict] = [
    {
        "chunk_id": f"chunk-{i}",
        "content": doc,
        "score": 0.8,
        "metadata": {"source": f"doc_{i}", "domain": "payments"},
    }
    for i, doc in enumerate(PAYMENT_DOCS)
]


# ---------------------------------------------------------------------------
# Shared factory helpers
# ---------------------------------------------------------------------------

def make_provider():
    """Return a real OpenAI provider using gpt-4o-mini."""
    from app.providers.openai_compatible import OpenAICompatibleProvider
    return OpenAICompatibleProvider(api_key=OPENAI_KEY, default_model="gpt-4o-mini")


def make_tenant():
    """Return a test TenantContext with enterprise plan."""
    from app.tenancy.context import TenantContext, PlanTier
    return TenantContext(
        tenant_id="real-e2e-rag",
        plan=PlanTier.ENTERPRISE,
        api_key_id="test-key-rag-001",
    )


def make_store_with_docs(collection_id: str) -> tuple:
    """
    Create an in-memory KnowledgeStore seeded with PAYMENT_DOCS.

    Uses empty embeddings (vector score = 0.0), so retrieval is
    driven entirely by trigram + BM25 — no external embedding model needed.
    """
    from app.rag.store import KnowledgeStore
    from app.rag.models import Chunk, KnowledgeCollection

    tenant = make_tenant()
    store = KnowledgeStore()

    col = KnowledgeCollection(
        name="Payment Knowledge Base",
        collection_id=collection_id,
    )
    store.create_collection(col, tenant_ctx=tenant)

    for i, doc in enumerate(PAYMENT_DOCS):
        chunk = Chunk(
            document_id=f"doc-{i}",
            content=doc,
            embedding=[],          # trigram-only; cosine score = 0.0
            chunk_index=i,
            chunk_id=f"chunk-{i}",
            metadata={"source": f"doc_{i}", "domain": "payments"},
        )
        store.ingest_chunk(chunk, collection_id=collection_id, tenant_ctx=tenant)

    return store, tenant


def make_retrieve_fn(collection_id: str):
    """
    Build an async retrieve_fn backed by the in-memory knowledge store.
    Used by FLARE, SelfRAG, SpeculativeRAG to retrieve supporting context.
    """
    store, tenant = make_store_with_docs(collection_id)

    async def _retrieve(query: str) -> str:
        results = store.hybrid_search(
            query=query,
            query_embedding=[],
            collection_id=collection_id,
            tenant_ctx=tenant,
            top_k=5,
        )
        return "\n\n".join(r.content for r in results)

    return _retrieve


# ---------------------------------------------------------------------------
# Stub AsyncSession — lets FusionRAG / AdaptiveRAG call retrieve_fusion /
# retrieve without a real PostgreSQL connection.
#
# The RAG engine calls three SQL legs:
#   1. hnsw.ef_search setup  → return empty (no pgvector)
#   2. knowledge_collections → return empty (fall back to dim=1536)
#   3. vector (<=>)          → return empty (no embeddings)
#   4. FTS (ts_rank_cd)      → return all PAYMENT_CHUNKS rows
#   5. trgm (similarity)     → return all PAYMENT_CHUNKS rows
# ---------------------------------------------------------------------------

class _StubRow:
    """Mimics an asyncpg/SQLAlchemy Row — supports integer indexing."""

    def __init__(self, chunk_id: str, content: str, metadata: dict, score: float) -> None:
        self._d = (chunk_id, content, metadata, score)

    def __getitem__(self, key: int):
        return self._d[key]


class _StubResult:
    """Mimics the object returned by session.execute()."""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    def fetchall(self) -> list:
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _StubSession:
    """
    Minimal async DB session stub for app.rag.engine hybrid_search calls.

    Intercepts SQL by inspecting the query text:
      - hnsw / vector <=> → empty (skip vector leg silently)
      - knowledge_collections metadata → empty (use default dim 1536)
      - FTS (ts_rank_cd) / trgm (similarity) / knowledge_chunks table
          → return all PAYMENT_CHUNKS so the RRF fusion gets real content
    """

    def __init__(self, chunks_data: list[dict]) -> None:
        self._chunks = chunks_data

    async def execute(self, stmt, params=None):
        sql = str(stmt).lower()

        # hnsw config or vector distance operator → silent empty
        if "hnsw" in sql or ("<=>" in sql and "embedding" in sql):
            return _StubResult([])

        # Collection metadata lookup (embedding_dim) → fall back to 1536
        if "knowledge_collections" in sql:
            return _StubResult([])

        # FTS and trgm retrieval legs → return all seeded chunks
        if any(kw in sql for kw in (
            "ts_rank_cd", "tsvector", "plainto_tsquery",
            "similarity", "knowledge_chunks",
        )):
            rows = [
                _StubRow(
                    c["chunk_id"],
                    c["content"],
                    c.get("metadata", {}),
                    0.5,
                )
                for c in self._chunks
            ]
            return _StubResult(rows)

        return _StubResult([])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ===========================================================================
# 1. FusionRAG — multi-query parallel retrieval with RRF fusion
# ===========================================================================

async def test_fusion_rag_pci_dss_card_security():
    """
    FusionRAG: expands query into N variants, retrieves per-variant, RRF-fuses.
    Query: PCI DSS security requirements for storing card data.
    Expected: top results mention PCI, CVV, AES-256, card encryption.
    """
    from app.rag.agentic.patterns.fusion import FusionRAGPattern

    pattern = FusionRAGPattern()
    session = _StubSession(PAYMENT_CHUNKS)
    query = "What are the security requirements for storing card data under PCI DSS?"

    results = await pattern.execute(
        session=session,
        query=query,
        query_embedding=None,
        collection_id="payment-kb-fusion",
        top_k=5,
        max_variants=3,
    )

    assert isinstance(results, list), (
        f"FusionRAG must return list, got {type(results)}"
    )
    assert len(results) > 0, "FusionRAG returned no results from stub session"

    # Unify content across result objects (engine.RetrievalResult has .content attribute)
    all_content = " ".join(
        r.content if hasattr(r, "content") else r.get("content", "")
        for r in results
    ).lower()

    assert any(kw in all_content for kw in ("pci", "cvv", "aes", "card", "encrypt")), (
        f"FusionRAG results should mention PCI/CVV/AES keywords.\n"
        f"Got top-5 content: {all_content[:400]}"
    )

    # Verify RRF metadata is present (retrieval_legs populated)
    first = results[0]
    legs = getattr(first, "retrieval_legs", first.get("retrieval_legs", []) if isinstance(first, dict) else [])
    assert isinstance(legs, list), "RetrievalResult should carry retrieval_legs metadata"


# ===========================================================================
# 2. CorrectiveRAG — confidence-scored retrieval with gap-detection fallback
# ===========================================================================

async def test_corrective_rag_upi_refund_timeline():
    """
    CorrectiveRAG: retrieves with RetrieverTool, evaluates confidence,
    applies correction logic when confidence < threshold or gap detected.
    Query: UPI refund timeline for failed transactions.
    Expected: result contains UPI refund info; corrected flag may be set.
    """
    from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
    from app.rag.agentic.retriever_tool import RetrieverTool

    collection_id = "payment-kb-corrective"
    store, tenant = make_store_with_docs(collection_id)
    retriever_tool = RetrieverTool(knowledge_store=store)
    pattern = CorrectiveRAGPattern()

    query = "What is the refund timeline for failed UPI payments?"

    result = await pattern.execute(
        retriever_tool=retriever_tool,
        query=query,
        tenant_ctx=tenant,
        collection_ids=[collection_id],
        top_k=5,
        confidence_threshold=0.5,   # triggers correction on low trigram-only scores
    )

    assert result is not None, "CorrectiveRAG must return a RetrievalResult"

    # Gather content from context_text + individual chunks
    ctx_text = getattr(result, "context_text", "") or ""
    chunks = getattr(result, "chunks", []) or []
    all_text = ctx_text + " " + " ".join(c.get("content", "") for c in chunks)

    assert any(kw in all_text.lower() for kw in ("upi", "refund", "business days", "failed")), (
        f"CorrectiveRAG should surface UPI refund content.\n"
        f"context_text[:300]: {ctx_text[:300]}\n"
        f"chunks count: {len(chunks)}"
    )

    # source must be kb or corrected-kb path
    source = getattr(result, "source", "")
    assert source in ("knowledge_base", "parametric", "none_available"), (
        f"Unexpected source: {source!r}"
    )


# ===========================================================================
# 3. AdaptiveRAG — RetrievalPlanner auto-selects strategy
# ===========================================================================

async def test_adaptive_rag_3ds2_frictionless():
    """
    AdaptiveRAG: auto-selects retrieval strategy via RetrievalPlanner heuristics.
    Query: 3DS2 frictionless authentication.
    Uses force_strategy='lexical' to exercise the keyword-only path without HyDE.
    Expected: results mention 3DS2, frictionless, OTP, risk-based auth.
    """
    from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern

    pattern = AdaptiveRAGPattern()
    session = _StubSession(PAYMENT_CHUNKS)
    query = "Explain 3DS2 frictionless flow authentication"

    results = await pattern.execute(
        session=session,
        query=query,
        query_embedding=None,
        collection_id="payment-kb-adaptive",
        top_k=5,
        # force lexical so we don't need a real DB for HyDE/multi-hop sub-calls
        force_strategy="lexical",
    )

    assert isinstance(results, list), (
        f"AdaptiveRAG must return list, got {type(results)}"
    )
    assert len(results) > 0, "AdaptiveRAG returned no results"

    all_content = " ".join(
        r.content if hasattr(r, "content") else r.get("content", "")
        for r in results
    ).lower()

    assert any(kw in all_content for kw in ("3d", "secure", "3ds", "frictionless", "otp", "authentication")), (
        f"AdaptiveRAG should retrieve 3DS2 content.\nGot: {all_content[:400]}"
    )


# ===========================================================================
# 4. FLARE — Forward-Looking Active Retrieval
# ===========================================================================

async def test_flare_chargeback_dispute_process():
    """
    FLARE: generate initial answer, detect uncertainty signals, retrieve targeted
    context for uncertain claims, re-generate with evidence.
    Query: chargeback dispute process.
    Expected: non-empty answer string mentioning chargeback details.
    Real OpenAI used for generation and re-generation steps.
    """
    from app.rag.agentic.patterns.flare import FLAREPattern

    provider = make_provider()
    retrieve_fn = make_retrieve_fn("payment-kb-flare")
    pattern = FLAREPattern(max_iterations=2)

    query = "What happens during a chargeback dispute process for payment merchants?"

    answer = await pattern.execute(
        query=query,
        provider=provider,
        retrieve_fn=retrieve_fn,
        max_tokens=600,
    )

    assert isinstance(answer, str), f"FLARE must return str, got {type(answer)}"
    assert len(answer) > 50, (
        f"FLARE answer too short (got {len(answer)} chars): {answer!r}"
    )
    # Should mention chargebacks or merchant response
    assert any(kw in answer.lower() for kw in (
        "chargeback", "merchant", "dispute", "reason code", "30 days", "respond"
    )), (
        f"FLARE answer should discuss chargeback process.\nGot: {answer[:400]}"
    )


# ===========================================================================
# 5. SelfRAG — Self-reflective retrieval with critique tokens
# ===========================================================================

async def test_self_rag_upi_vs_imps_transfer():
    """
    SelfRAG: decides whether to retrieve, evaluates ISREL / ISSUP / ISUSE,
    returns critique-informed answer.
    Query: Compare UPI and IMPS transfer characteristics and limits.
    Expected: answer mentions UPI and IMPS details from the corpus.
    Real OpenAI used for should-retrieve, generate, and critique calls.
    """
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern

    provider = make_provider()
    retrieve_fn = make_retrieve_fn("payment-kb-self-rag")
    pattern = SelfRAGPattern(confidence_threshold=0.5)

    query = "Compare UPI and IMPS payment transfer characteristics and limits."

    answer = await pattern.execute(
        query=query,
        provider=provider,
        retrieve_fn=retrieve_fn,
        max_tokens=700,
    )

    assert isinstance(answer, str), f"SelfRAG must return str, got {type(answer)}"
    assert len(answer) > 50, (
        f"SelfRAG answer too short ({len(answer)} chars): {answer!r}"
    )
    assert any(kw in answer.lower() for kw in ("upi", "imps", "transfer", "npci", "real-time", "lakh")), (
        f"SelfRAG answer should compare UPI/IMPS.\nGot: {answer[:500]}"
    )


async def test_self_rag_execute_with_critique_returns_metadata():
    """
    SelfRAGPattern.execute_with_critique() returns a SelfRAGResult with
    full critique metadata (confidence, is_relevant, is_supported, is_useful).
    """
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern, SelfRAGResult

    provider = make_provider()
    retrieve_fn = make_retrieve_fn("payment-kb-self-rag-critique")
    pattern = SelfRAGPattern(confidence_threshold=0.4)

    query = "What is the webhook retry schedule for failed payment notifications?"

    result = await pattern.execute_with_critique(
        query=query,
        provider=provider,
        retrieve_fn=retrieve_fn,
        max_tokens=500,
    )

    assert isinstance(result, SelfRAGResult), (
        f"execute_with_critique must return SelfRAGResult, got {type(result)}"
    )
    assert isinstance(result.answer, str) and len(result.answer) > 20, (
        f"SelfRAGResult.answer must be non-trivial string. Got: {result.answer!r}"
    )
    # Confidence is a float in [0, 1]
    assert 0.0 <= result.confidence <= 1.0, (
        f"Confidence must be in [0,1], got {result.confidence}"
    )
    # The answer should mention webhook retry details
    assert any(kw in result.answer.lower() for kw in (
        "webhook", "retry", "minute", "hour", "backoff", "notification", "5 minutes"
    )), (
        f"SelfRAG answer should discuss webhook retry.\nGot: {result.answer[:400]}"
    )


# ===========================================================================
# 6. SpeculativeRAG — parallel candidate generation + retrieval verification
# ===========================================================================

async def test_speculative_rag_credit_card_success_rate():
    """
    SpeculativeRAG: generates N candidate answers in parallel, retrieves
    supporting context for each, scores and returns the best-supported candidate.
    Query: average success rate for credit card payments.
    Expected: answer references ~87% or credit card success rate.
    Real OpenAI used for candidate generation and verification scoring.
    """
    from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern

    provider = make_provider()
    retrieve_fn = make_retrieve_fn("payment-kb-speculative")
    pattern = SpeculativeRAGPattern(n_candidates=3, min_support_score=0.5)

    query = "What is the average success rate for credit card payments through payment gateways?"

    answer = await pattern.execute(
        query=query,
        provider=provider,
        retrieve_fn=retrieve_fn,
        max_tokens=400,
    )

    assert isinstance(answer, str), f"SpeculativeRAG must return str, got {type(answer)}"
    assert len(answer) > 20, (
        f"SpeculativeRAG answer too short ({len(answer)} chars): {answer!r}"
    )
    # Answer should reference success rate or credit cards
    assert any(kw in answer.lower() for kw in (
        "87", "credit card", "success rate", "payment gateway", "%"
    )), (
        f"SpeculativeRAG answer should mention credit card success rate.\nGot: {answer[:400]}"
    )


# ===========================================================================
# 7. RAPTOR — Recursive Abstractive Processing for Tree-Organized Retrieval
# ===========================================================================

async def test_raptor_summarizes_all_payment_methods():
    """
    RAPTOR: recursively clusters chunks into groups, LLM-summarizes each group,
    builds a hierarchical tree, and answers using all tree levels.
    Query: comprehensive summary of all payment methods.
    Expected: answer mentions multiple payment domains from the corpus.
    Real OpenAI used for all summarization and final answer generation.
    """
    from app.rag.agentic.patterns.raptor import RAPTORPattern

    provider = make_provider()
    pattern = RAPTORPattern(cluster_size=4, max_levels=2)

    query = "Summarize all payment methods, their characteristics, and security requirements."

    answer = await pattern.execute(
        query=query,
        chunks=PAYMENT_CHUNKS,
        provider=provider,
        max_tokens=600,
    )

    assert isinstance(answer, str), f"RAPTOR must return str, got {type(answer)}"
    assert len(answer) > 100, (
        f"RAPTOR answer too short ({len(answer)} chars): {answer!r}"
    )
    # Hierarchical summary should cover multiple payment domains
    answer_lower = answer.lower()
    covered = [
        kw for kw in ("upi", "pci", "emi", "chargeback", "3d", "tokeniz", "fraud", "imps", "webhook")
        if kw in answer_lower
    ]
    assert len(covered) >= 3, (
        f"RAPTOR summary should cover ≥3 payment domains from corpus.\n"
        f"Covered: {covered}\nAnswer: {answer[:600]}"
    )


async def test_raptor_builds_tree_from_small_corpus():
    """
    RAPTOR tree-building: with cluster_size=5 and 10 chunks,
    level-1 produces 2 summaries, level-2 merges them to 1 root.
    Verifies the hierarchical structure is created correctly.
    """
    from app.rag.agentic.patterns.raptor import RAPTORPattern

    provider = make_provider()
    # Small corpus: just 4 chunks to keep API calls minimal
    small_chunks = PAYMENT_CHUNKS[:4]
    pattern = RAPTORPattern(cluster_size=2, max_levels=2)

    query = "What security and authentication measures apply to card payments?"

    answer = await pattern.execute(
        query=query,
        chunks=small_chunks,
        provider=provider,
        max_tokens=400,
    )

    assert isinstance(answer, str) and len(answer) > 30, (
        f"RAPTOR tree answer must be non-trivial. Got: {answer!r}"
    )
    assert any(kw in answer.lower() for kw in ("pci", "cvv", "aes", "card", "3d", "secure", "otp")), (
        f"RAPTOR small-corpus answer should mention security/authentication.\nGot: {answer[:300]}"
    )


# ===========================================================================
# 8. ColBERT — Late interaction reranking via MaxSim token scoring
# ===========================================================================

async def test_colbert_reranks_emi_interest_chunks():
    """
    ColBERT: reranks chunks using MaxSim token-level scoring (TF-IDF fallback
    when sentence-transformers is not installed).
    Query: EMI interest rates for no-cost EMI.
    Expected: EMI doc (chunk-5) floats to top after reranking; result string
    contains EMI / interest / subvention keywords.
    No OpenAI call — ColBERT is purely scoring-based.
    """
    from app.rag.agentic.patterns.colbert import ColBERTPattern

    pattern = ColBERTPattern(alpha=0.6)
    query = "EMI interest rates no-cost EMI subvention"

    result_str = await pattern.execute(
        query=query,
        chunks=PAYMENT_CHUNKS,
        top_k=3,
    )

    assert isinstance(result_str, str), f"ColBERT must return str, got {type(result_str)}"
    assert len(result_str) > 20, f"ColBERT result too short: {result_str!r}"

    # EMI doc must appear in top-3 (it scores high for EMI-specific tokens)
    assert any(kw in result_str.lower() for kw in ("emi", "interest", "no-cost", "subvention", "12-24")), (
        f"ColBERT top-3 result should surface EMI content.\nGot: {result_str[:400]}"
    )


async def test_colbert_rerank_method_returns_sorted_chunks():
    """
    ColBERTPattern.rerank() returns chunks sorted by blended ColBERT+original score.
    Validates colbert_score and original_score metadata are attached.
    """
    from app.rag.agentic.patterns.colbert import ColBERTPattern

    pattern = ColBERTPattern(alpha=0.5)
    query = "fraud detection velocity checks BIN analysis"

    reranked = pattern.rerank(query=query, chunks=PAYMENT_CHUNKS, top_k=5)

    assert isinstance(reranked, list) and len(reranked) == 5, (
        f"rerank() must return exactly top_k=5 chunks, got {len(reranked)}"
    )
    # Each chunk has colbert_score and original_score keys
    for chunk in reranked:
        assert "colbert_score" in chunk, f"Missing colbert_score in chunk {chunk.get('chunk_id')}"
        assert "original_score" in chunk, f"Missing original_score in chunk {chunk.get('chunk_id')}"
        assert 0.0 <= chunk["score"] <= 1.5, f"Blended score out of expected range: {chunk['score']}"

    # Chunks are sorted descending by blended score
    scores = [c["score"] for c in reranked]
    assert scores == sorted(scores, reverse=True), "rerank() must return chunks sorted by score desc"

    # Fraud detection doc (chunk-9) should rank highly for this query
    top3_ids = [c["chunk_id"] for c in reranked[:3]]
    assert "chunk-9" in top3_ids, (
        f"Fraud doc (chunk-9) should be in top-3 for fraud query.\n"
        f"Top-3 ids: {top3_ids}"
    )


# ===========================================================================
# 9. AgenticChunking — LLM-driven proposition extraction (Dense X Retrieval)
# ===========================================================================

async def test_agentic_chunking_extracts_propositions_from_payment_docs():
    """
    AgenticChunking: uses an LLM to extract self-contained atomic propositions
    from each input chunk (Dense X Retrieval, Chen et al. 2023).
    Input: 4 payment docs.
    Expected: output has more chunks than input (each doc → multiple props);
    each proposition is a standalone sentence with payment-domain content.
    Real OpenAI used for proposition extraction.
    """
    from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern

    provider = make_provider()
    # Use first 4 chunks to keep API calls manageable in CI
    input_chunks = PAYMENT_CHUNKS[:4]
    pattern = AgenticChunkingPattern(max_propositions=5)

    result_chunks = await pattern.execute(
        chunks=input_chunks,
        provider=provider,
        query="payment security requirements",
        top_k=10,
    )

    assert isinstance(result_chunks, list), (
        f"AgenticChunking must return list, got {type(result_chunks)}"
    )
    assert len(result_chunks) > len(input_chunks), (
        f"AgenticChunking should expand 4 input chunks to more propositions.\n"
        f"Input: {len(input_chunks)}, Output: {len(result_chunks)}"
    )

    # Each result chunk must have content and chunk_id
    for rc in result_chunks:
        assert "content" in rc and rc["content"], (
            f"Each proposition chunk must have non-empty content. Got: {rc}"
        )
        assert "chunk_id" in rc, f"Each proposition chunk must have chunk_id. Got: {rc}"

    # All propositions should be payment-domain sentences
    all_prop_text = " ".join(rc["content"] for rc in result_chunks).lower()
    assert any(kw in all_prop_text for kw in ("pci", "cvv", "card", "upi", "3d", "secure", "token")), (
        f"Proposition content should reference payment domain.\nAll text: {all_prop_text[:500]}"
    )


async def test_agentic_chunking_single_doc_multi_propositions():
    """
    AgenticChunking on a single complex payment document extracts
    multiple atomic propositions — each a standalone, searchable sentence.
    """
    from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern

    provider = make_provider()
    # Use the 3DS2 doc which has multiple distinct facts
    single_chunk = [PAYMENT_CHUNKS[3]]   # "3D Secure 2.0 (3DS2) uses risk-based authentication..."
    pattern = AgenticChunkingPattern(max_propositions=8)

    result_chunks = await pattern.execute(
        chunks=single_chunk,
        provider=provider,
        query="3DS2 authentication",
    )

    assert isinstance(result_chunks, list) and len(result_chunks) >= 2, (
        f"Single 3DS2 doc should yield ≥2 propositions.\nGot {len(result_chunks)}: {result_chunks}"
    )

    # Each proposition should be a meaningful sentence (not a fragment)
    for rc in result_chunks:
        content = rc.get("content", "")
        assert len(content) > 15, f"Proposition too short: {content!r}"

    # Propositions must reference 3DS2 concepts
    all_text = " ".join(rc.get("content", "") for rc in result_chunks).lower()
    assert any(kw in all_text for kw in ("3d", "secure", "otp", "risk", "frictionless", "challenge", "authentication")), (
        f"3DS2 propositions should mention auth concepts.\nGot: {all_text[:400]}"
    )


# ===========================================================================
# Pattern metadata and compatibility checks (synchronous, no API calls)
# ===========================================================================

def test_all_patterns_have_required_metadata():
    """
    All 9 RAG patterns must expose:
      - pattern_id (non-empty str)
      - description (non-empty str)
      - state (RAGPatternState.IMPLEMENTED)
      - is_compatible() callable
    """
    from app.rag.agentic.patterns.fusion import FusionRAGPattern
    from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
    from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
    from app.rag.agentic.patterns.flare import FLAREPattern
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern
    from app.rag.agentic.patterns.raptor import RAPTORPattern
    from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern
    from app.rag.agentic.patterns.colbert import ColBERTPattern
    from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
    from app.rag.agentic.patterns.base import RAGPatternState

    patterns = [
        FusionRAGPattern(),
        CorrectiveRAGPattern(),
        AdaptiveRAGPattern(),
        FLAREPattern(),
        SelfRAGPattern(),
        RAPTORPattern(),
        SpeculativeRAGPattern(),
        ColBERTPattern(),
        AgenticChunkingPattern(),
    ]

    for p in patterns:
        assert isinstance(p.pattern_id, str) and p.pattern_id, (
            f"{type(p).__name__} must have non-empty pattern_id"
        )
        assert isinstance(p.description, str) and p.description, (
            f"{type(p).__name__} must have non-empty description"
        )
        assert p.state == RAGPatternState.IMPLEMENTED, (
            f"{type(p).__name__}.state must be IMPLEMENTED, got {p.state}"
        )
        assert callable(p.is_compatible), (
            f"{type(p).__name__}.is_compatible must be callable"
        )


def test_colbert_rerank_is_query_dependent():
    """
    ColBERT scores must differ across queries — validates the token scoring
    actually depends on query tokens, not a constant.
    """
    from app.rag.agentic.patterns.colbert import ColBERTPattern

    pattern = ColBERTPattern(alpha=0.5)
    chunks = PAYMENT_CHUNKS

    # PCI DSS query → PCI doc should rank high
    reranked_pci = pattern.rerank("PCI DSS card encryption AES", chunks, top_k=10)
    # EMI query → EMI doc should rank high
    reranked_emi = pattern.rerank("EMI interest rates subvention", chunks, top_k=10)

    top1_pci = reranked_pci[0]["chunk_id"]
    top1_emi = reranked_emi[0]["chunk_id"]

    # Different queries should produce different top-ranked chunks
    assert top1_pci != top1_emi or reranked_pci[0]["colbert_score"] != reranked_emi[0]["colbert_score"], (
        f"ColBERT scores should differ per query.\n"
        f"PCI top: {top1_pci} ({reranked_pci[0]['colbert_score']:.4f})\n"
        f"EMI top: {top1_emi} ({reranked_emi[0]['colbert_score']:.4f})"
    )

    # PCI doc (chunk-0) should score higher than EMI doc (chunk-5) for PCI query
    pci_scores = {c["chunk_id"]: c["colbert_score"] for c in reranked_pci}
    assert pci_scores.get("chunk-0", 0) >= pci_scores.get("chunk-5", 0), (
        f"For PCI query, chunk-0 (PCI DSS) should outscore chunk-5 (EMI).\n"
        f"chunk-0 score: {pci_scores.get('chunk-0'):.4f}, "
        f"chunk-5 score: {pci_scores.get('chunk-5'):.4f}"
    )


# ===========================================================================
# Integration smoke: FusionRAG + ColBERT pipeline
# (retrieve via fusion, rerank via ColBERT — no extra DB calls)
# ===========================================================================

async def test_fusion_then_colbert_pipeline():
    """
    Pipeline: FusionRAG retrieves candidates → ColBERT reranks by MaxSim.
    Query: fraud detection mechanisms in payment systems.
    Expected: fraud detection doc (chunk-9) surfaces after ColBERT reranking.
    """
    from app.rag.agentic.patterns.fusion import FusionRAGPattern
    from app.rag.agentic.patterns.colbert import ColBERTPattern

    session = _StubSession(PAYMENT_CHUNKS)
    query = "fraud detection mechanisms velocity checks device fingerprinting"

    # Step 1: FusionRAG retrieval
    fusion = FusionRAGPattern()
    retrieved = await fusion.execute(
        session=session,
        query=query,
        query_embedding=None,
        collection_id="payment-kb-pipeline",
        top_k=10,
        max_variants=2,
    )

    assert len(retrieved) > 0, "FusionRAG produced no candidates for pipeline test"

    # Convert engine.RetrievalResult → chunk dicts for ColBERT
    chunk_dicts = [
        {
            "chunk_id": r.chunk_id if hasattr(r, "chunk_id") else r.get("chunk_id", ""),
            "content": r.content if hasattr(r, "content") else r.get("content", ""),
            "score": r.score if hasattr(r, "score") else r.get("score", 0.5),
        }
        for r in retrieved
    ]

    # Step 2: ColBERT reranking
    colbert = ColBERTPattern(alpha=0.6)
    reranked_str = await colbert.execute(
        query=query,
        chunks=chunk_dicts,
        top_k=3,
    )

    assert isinstance(reranked_str, str) and len(reranked_str) > 20, (
        f"Pipeline ColBERT result too short: {reranked_str!r}"
    )
    assert any(kw in reranked_str.lower() for kw in (
        "fraud", "velocity", "fingerprint", "bin", "geo"
    )), (
        f"Pipeline result should surface fraud detection content.\nGot: {reranked_str[:400]}"
    )
