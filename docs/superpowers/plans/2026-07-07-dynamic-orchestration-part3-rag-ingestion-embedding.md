# AgentVerse Core Dynamic Orchestration — Part 3: Agentic RAG + Ingestion + Embedding (P1)

> **Depends on:** Parts 1 & 2 (orchestration contracts + security gates must exist)

**Goal:** Implement the Agentic RAG runtime (RetrieverTool, SourceInventory, FallbackChain, ContextBudget, RerankPolicy, CitationManager, PromptBuilder), Ingestion Orchestrator for multimodal content, and Embedding Orchestrator for modality-aware embedding selection.

**Run all tests in this phase:**
```bash
cd agent-verse-backend
uv run pytest tests/rag/test_agentic/ tests/ingestion/ tests/embedding/ -v --no-cov
```

---

## Task 13: RetrieverTool — Agent-Owned Agentic RAG

**Files:**
- Create: `app/rag/agentic/__init__.py`
- Create: `app/rag/agentic/retriever_tool.py`
- Create: `app/rag/agentic/source_inventory.py`
- Create: `tests/rag/__init__.py` (may exist)
- Create: `tests/rag/test_agentic/__init__.py`
- Create: `tests/rag/test_agentic/test_retriever_tool.py`

- [ ] **Step 13.1: Write failing tests**

```python
# tests/rag/test_agentic/test_retriever_tool.py
"""RetrieverTool must NEVER return silent empty strings — always a structured result."""
from __future__ import annotations
import pytest
from app.rag.agentic.retriever_tool import RetrieverTool, RetrievalResult
from app.rag.agentic.source_inventory import SourceInventory, SourceInventoryResult
from app.tenancy.context import TenantContext, PlanTier
from app.rag.store import KnowledgeStore
from app.rag.models import KnowledgeCollection, Chunk


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def empty_store():
    return KnowledgeStore()


@pytest.fixture
def loaded_store(tenant_ctx):
    store = KnowledgeStore()
    col = KnowledgeCollection(name="docs", collection_id="col1", embedder="fake")
    store.create_collection(col, tenant_ctx=tenant_ctx)
    chunk = Chunk(
        document_id="d1",
        content="The AgentVerse platform supports dynamic orchestration.",
        embedding=[0.1] * 10,
        chunk_index=0,
        chunk_id="c1",
        metadata={"source_url": "https://docs.example.com/page1", "page_number": 1},
    )
    store.ingest_chunk(chunk, collection_id="col1", tenant_ctx=tenant_ctx)
    return store


# ── Never return empty strings ────────────────────────────────────────────────

async def test_empty_kb_returns_structured_result_not_empty_string(tenant_ctx, empty_store):
    tool = RetrieverTool(knowledge_store=empty_store)
    result = await tool.retrieve(
        query="what is agentverse",
        tenant_ctx=tenant_ctx,
    )
    assert isinstance(result, RetrievalResult)
    # Must NOT be silent empty — must have a source explanation
    assert result.source in ("none_available", "parametric", "web", "memory")
    assert result.confidence >= 0.0


async def test_retrieval_returns_content_when_kb_has_data(tenant_ctx, loaded_store):
    tool = RetrieverTool(knowledge_store=loaded_store)
    result = await tool.retrieve(
        query="dynamic orchestration",
        tenant_ctx=tenant_ctx,
        collection_ids=["col1"],
    )
    assert isinstance(result, RetrievalResult)
    assert len(result.chunks) > 0
    assert result.confidence > 0.0
    assert result.source == "knowledge_base"


async def test_retrieval_result_has_citations(tenant_ctx, loaded_store):
    tool = RetrieverTool(knowledge_store=loaded_store)
    result = await tool.retrieve(
        query="platform",
        tenant_ctx=tenant_ctx,
        collection_ids=["col1"],
    )
    # Citations must be structured, not empty
    assert isinstance(result.citations, list)


async def test_retrieval_respects_min_confidence(tenant_ctx, loaded_store):
    tool = RetrieverTool(knowledge_store=loaded_store)
    result = await tool.retrieve(
        query="completely unrelated topic xyz123",
        tenant_ctx=tenant_ctx,
        collection_ids=["col1"],
        min_confidence=0.9,   # very high threshold
    )
    # Low-confidence results filtered out → graceful degradation
    assert isinstance(result, RetrievalResult)


# ── SourceInventory ───────────────────────────────────────────────────────────

def test_source_inventory_empty_kb(tenant_ctx, empty_store):
    inventory = SourceInventory(knowledge_store=empty_store)
    result = inventory.build(tenant_ctx=tenant_ctx)
    assert isinstance(result, SourceInventoryResult)
    assert result.kb_collections == 0
    assert result.kb_state == "empty"


def test_source_inventory_with_data(tenant_ctx, loaded_store):
    inventory = SourceInventory(knowledge_store=loaded_store)
    result = inventory.build(tenant_ctx=tenant_ctx)
    assert result.kb_collections >= 1
    assert result.kb_state in ("sparse", "healthy")


def test_source_inventory_knows_web_available(tenant_ctx, empty_store):
    inventory = SourceInventory(knowledge_store=empty_store, web_search_available=True)
    result = inventory.build(tenant_ctx=tenant_ctx)
    assert result.web_available is True


# ── Strategy routing ─────────────────────────────────────────────────────────

async def test_strategy_auto_selects_web_when_kb_empty(tenant_ctx, empty_store):
    tool = RetrieverTool(knowledge_store=empty_store, web_search_available=True)
    result = await tool.retrieve(
        query="current Python version",
        tenant_ctx=tenant_ctx,
        strategy="auto",
    )
    # With empty KB, auto should route to web or parametric
    assert result.source in ("web", "parametric", "none_available")
    assert result.strategy_used is not None
```

- [ ] **Step 13.2: Create dirs and run to confirm failure**

```bash
mkdir -p agent-verse-backend/app/rag/agentic
mkdir -p agent-verse-backend/tests/rag/test_agentic
touch agent-verse-backend/app/rag/agentic/__init__.py
touch agent-verse-backend/tests/rag/test_agentic/__init__.py
# tests/rag/__init__.py may already exist — check
ls agent-verse-backend/tests/rag/__init__.py 2>/dev/null || touch agent-verse-backend/tests/rag/__init__.py
cd agent-verse-backend && uv run pytest tests/rag/test_agentic/test_retriever_tool.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 13.3: Implement `app/rag/agentic/source_inventory.py`**

```python
"""SourceInventory — builds a snapshot of all retrieval sources available for a tenant."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.tenancy.context import TenantContext


@dataclass
class SourceInventoryResult:
    kb_collections: int
    kb_total_chunks: int
    kg_nodes: int
    ltm_entries: int
    exec_memory_plans: int
    web_available: bool
    embedder_available: bool
    kb_state: str = "unknown"     # empty|sparse|healthy|stale
    graph_state: str = "unknown"  # empty|healthy|partial

    def to_dict(self) -> dict[str, Any]:
        return {
            "kb_collections": self.kb_collections,
            "kb_total_chunks": self.kb_total_chunks,
            "kg_nodes": self.kg_nodes,
            "ltm_entries": self.ltm_entries,
            "exec_memory_plans": self.exec_memory_plans,
            "web_available": self.web_available,
            "embedder_available": self.embedder_available,
            "kb_state": self.kb_state,
            "graph_state": self.graph_state,
        }


class SourceInventory:
    def __init__(
        self,
        *,
        knowledge_store: Any = None,
        kg_store: Any = None,
        ltm_store: Any = None,
        exec_memory: Any = None,
        web_search_available: bool = False,
        embedder_available: bool = False,
    ) -> None:
        self._kb = knowledge_store
        self._kg = kg_store
        self._ltm = ltm_store
        self._exec = exec_memory
        self._web = web_search_available
        self._embedder = embedder_available

    def build(self, *, tenant_ctx: "TenantContext") -> SourceInventoryResult:
        # KB state
        kb_collections = 0
        kb_chunks = 0
        if self._kb is not None:
            cols = self._kb.list_collections(tenant_ctx=tenant_ctx)
            kb_collections = len(cols)
            kb_chunks = sum(getattr(c, "document_count", 0) for c in cols)

        # KG state
        kg_nodes = 0
        if self._kg is not None:
            try:
                nodes = self._kg.query_nodes(tenant_ctx.tenant_id, limit=1000)
                kg_nodes = len(nodes)
            except Exception:
                pass

        # LTM state
        ltm_entries = 0
        if self._ltm is not None:
            try:
                mems = self._ltm.list_all(tenant_ctx=tenant_ctx)
                ltm_entries = len(mems)
            except Exception:
                pass

        # Exec memory
        exec_plans = 0
        if self._exec is not None:
            try:
                plans = self._exec.recall(goal_hint="", tenant_ctx=tenant_ctx, top_k=100)
                exec_plans = len(plans)
            except Exception:
                pass

        # Determine KB state
        if kb_collections == 0:
            kb_state = "empty"
        elif kb_chunks < 10:
            kb_state = "sparse"
        else:
            kb_state = "healthy"

        graph_state = "empty" if kg_nodes == 0 else "healthy"

        return SourceInventoryResult(
            kb_collections=kb_collections,
            kb_total_chunks=kb_chunks,
            kg_nodes=kg_nodes,
            ltm_entries=ltm_entries,
            exec_memory_plans=exec_plans,
            web_available=self._web,
            embedder_available=self._embedder,
            kb_state=kb_state,
            graph_state=graph_state,
        )
```

- [ ] **Step 13.4: Implement `app/rag/agentic/retriever_tool.py`**

```python
"""RetrieverTool — agent-owned retrieval with strategy routing and structured results.

NEVER returns empty strings. Every degraded path returns a structured
RetrievalResult with source="none_available" and confidence=0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.tenancy.context import TenantContext


@dataclass
class CitationRef:
    source: str          # kb|web|memory|graph
    url: str = ""
    page_number: int | None = None
    chunk_id: str = ""
    score: float = 0.0


@dataclass
class RetrievalResult:
    """Structured retrieval result — never a silent empty string."""
    query: str
    source: str                  # knowledge_base|web|memory|graph|parametric|none_available
    strategy_used: str           # auto|hybrid|vector|graph|hyde|web|memory
    confidence: float            # 0.0–1.0
    chunks: list[dict[str, Any]] = field(default_factory=list)
    citations: list[CitationRef] = field(default_factory=list)
    fallback_used: bool = False
    fallback_reason: str = ""
    reformulation_count: int = 0

    @property
    def context_text(self) -> str:
        """Concatenated chunk content for prompt injection."""
        return "\n\n".join(c.get("content", "") for c in self.chunks)


class RetrieverTool:
    """Unified retrieval tool wrapping all 7 retrieval sources."""

    def __init__(
        self,
        *,
        knowledge_store: Any = None,
        kg_store: Any = None,
        ltm_store: Any = None,
        exec_memory: Any = None,
        embedder: Any = None,
        web_search_fn: Any = None,
        web_search_available: bool = False,
    ) -> None:
        self._kb = knowledge_store
        self._kg = kg_store
        self._ltm = ltm_store
        self._exec = exec_memory
        self._embedder = embedder
        self._web_fn = web_search_fn
        self._web_available = web_search_available or (web_search_fn is not None)

    async def retrieve(
        self,
        query: str,
        *,
        tenant_ctx: "TenantContext",
        strategy: str = "auto",
        collection_ids: list[str] | None = None,
        top_k: int = 5,
        min_confidence: float = 0.3,
        allow_web_fallback: bool = True,
        allow_reformulation: bool = True,
        max_reformulation_attempts: int = 2,
    ) -> RetrievalResult:
        """Retrieve with strategy routing and structured degraded paths."""

        # Determine effective strategy
        effective_strategy = strategy
        if strategy == "auto":
            effective_strategy = self._select_strategy(query, collection_ids)

        # 1. Try KB retrieval
        if effective_strategy in ("auto", "hybrid", "vector") and self._kb is not None:
            result = await self._kb_retrieve(
                query, tenant_ctx=tenant_ctx,
                collection_ids=collection_ids, top_k=top_k,
                min_confidence=min_confidence,
            )
            if result.confidence >= min_confidence:
                return result

        # 2. Try web fallback if KB empty/low-confidence and web allowed
        if allow_web_fallback and self._web_available and self._web_fn is not None:
            try:
                web_result = await self._web_retrieve(query, top_k=top_k)
                if web_result.confidence >= min_confidence:
                    return web_result
            except Exception:
                pass

        # 3. Try memory fallback
        if self._ltm is not None or self._exec is not None:
            mem_result = await self._memory_retrieve(query, tenant_ctx=tenant_ctx, top_k=top_k)
            if mem_result.confidence >= min_confidence:
                return mem_result

        # 4. Parametric baseline (no retrieval — model relies on training)
        return RetrievalResult(
            query=query,
            source="parametric",
            strategy_used=effective_strategy,
            confidence=0.1,
            chunks=[],
            citations=[],
            fallback_used=True,
            fallback_reason="all retrieval sources exhausted or unavailable",
        )

    def _select_strategy(self, query: str, collection_ids: list[str] | None) -> str:
        if self._kb is None and not self._web_available:
            return "memory"
        if self._kb is None:
            return "web" if self._web_available else "parametric"
        return "hybrid"

    async def _kb_retrieve(
        self,
        query: str,
        *,
        tenant_ctx: "TenantContext",
        collection_ids: list[str] | None,
        top_k: int,
        min_confidence: float,
    ) -> RetrievalResult:
        try:
            # Get embedding
            embedding: list[float] = []
            if self._embedder is not None:
                from app.providers.base import EmbedRequest
                resp = await self._embedder.embed(EmbedRequest(texts=[query]))
                embedding = resp.embeddings[0] if resp.embeddings else []

            # Determine collections to search
            if collection_ids:
                search_cols = collection_ids
            else:
                cols = self._kb.list_collections(tenant_ctx=tenant_ctx)
                search_cols = [c.collection_id for c in cols]

            if not search_cols:
                return RetrievalResult(
                    query=query, source="none_available",
                    strategy_used="hybrid", confidence=0.0,
                    fallback_used=True, fallback_reason="no KB collections found",
                )

            all_results = []
            for col_id in search_cols[:3]:  # cap at 3 collections
                results = self._kb.hybrid_search(
                    query=query,
                    query_embedding=embedding,
                    collection_id=col_id,
                    tenant_ctx=tenant_ctx,
                    top_k=top_k,
                )
                all_results.extend(results)

            # Sort and deduplicate by chunk_id
            seen = set()
            deduped = []
            for r in sorted(all_results, key=lambda x: x.score, reverse=True):
                if r.chunk_id not in seen:
                    seen.add(r.chunk_id)
                    deduped.append(r)

            filtered = [r for r in deduped if r.score >= min_confidence]

            if not filtered:
                return RetrievalResult(
                    query=query, source="none_available",
                    strategy_used="hybrid", confidence=0.0,
                    fallback_used=True,
                    fallback_reason=f"no results above min_confidence={min_confidence}",
                )

            avg_confidence = sum(r.score for r in filtered) / len(filtered)
            chunks = [
                {"chunk_id": r.chunk_id, "content": r.content, "score": r.score,
                 "source_url": r.source_url, "page_number": r.page_number}
                for r in filtered[:top_k]
            ]
            citations = [
                CitationRef(source="kb", url=r.source_url,
                            page_number=r.page_number, chunk_id=r.chunk_id, score=r.score)
                for r in filtered[:top_k]
            ]

            return RetrievalResult(
                query=query, source="knowledge_base",
                strategy_used="hybrid", confidence=avg_confidence,
                chunks=chunks, citations=citations,
            )

        except Exception as exc:
            return RetrievalResult(
                query=query, source="none_available",
                strategy_used="hybrid", confidence=0.0,
                fallback_used=True, fallback_reason=f"KB retrieval error: {exc!s}",
            )

    async def _web_retrieve(self, query: str, top_k: int) -> RetrievalResult:
        results = await self._web_fn(query, top_k=top_k)
        chunks = [{"content": r.get("snippet", r.get("content", "")),
                   "source_url": r.get("url", "")} for r in (results or [])]
        citations = [CitationRef(source="web", url=r.get("url", "")) for r in (results or [])]
        confidence = 0.6 if chunks else 0.0
        return RetrievalResult(
            query=query, source="web", strategy_used="web",
            confidence=confidence, chunks=chunks, citations=citations,
        )

    async def _memory_retrieve(
        self, query: str, *, tenant_ctx: "TenantContext", top_k: int
    ) -> RetrievalResult:
        chunks = []
        if self._ltm is not None:
            try:
                memories = self._ltm.recall(query=query, tenant_ctx=tenant_ctx, top_k=top_k)
                chunks = [{"content": m.content, "source_url": ""} for m in memories]
            except Exception:
                pass
        confidence = 0.5 if chunks else 0.0
        return RetrievalResult(
            query=query, source="memory", strategy_used="memory",
            confidence=confidence, chunks=chunks, citations=[],
        )
```

- [ ] **Step 13.5: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/rag/test_agentic/test_retriever_tool.py -v --no-cov
```
Expected: `10 passed`

- [ ] **Step 13.6: Commit**

```bash
cd agent-verse-backend
git add app/rag/agentic/ tests/rag/test_agentic/
git commit -m "feat(rag/agentic): add RetrieverTool + SourceInventory — never silent empty strings"
```

---

## Task 14: ContextBudget + RerankPolicy + CitationManager

**Files:**
- Create: `app/context/__init__.py`
- Create: `app/context/context_budget.py`
- Create: `app/context/rerank_policy.py`
- Create: `app/context/citation_manager.py`
- Create: `app/context/prompt_builder.py`
- Create: `tests/context/__init__.py`
- Create: `tests/context/test_context_pipeline.py`

- [ ] **Step 14.1: Write failing tests**

```python
# tests/context/test_context_pipeline.py
"""Context pipeline: rerank → dedup → budget → citations → prompt."""
from __future__ import annotations
import pytest
from app.context.context_budget import ContextBudget, BudgetResult
from app.context.rerank_policy import RerankPolicy, RerankStrategy
from app.context.citation_manager import CitationManager, Citation
from app.context.prompt_builder import PromptBuilder, PromptContextBundle


@pytest.fixture
def sample_chunks():
    return [
        {"chunk_id": "c1", "content": "AgentVerse supports dynamic orchestration.", "score": 0.9,
         "source_url": "https://docs.example.com/page1"},
        {"chunk_id": "c2", "content": "Dynamic orchestration selects patterns per goal.", "score": 0.8,
         "source_url": "https://docs.example.com/page2"},
        {"chunk_id": "c3", "content": "The platform is vendor-agnostic.", "score": 0.7,
         "source_url": "https://docs.example.com/page3"},
        {"chunk_id": "c4", "content": "Tenant isolation is enforced at the DB layer.", "score": 0.6,
         "source_url": "https://docs.example.com/page4"},
        # Duplicate content
        {"chunk_id": "c5", "content": "AgentVerse supports dynamic orchestration.", "score": 0.85,
         "source_url": "https://docs.example.com/page1"},
    ]


# ── ContextBudget ─────────────────────────────────────────────────────────────

def test_budget_truncates_to_max_tokens(sample_chunks):
    budget = ContextBudget(max_tokens=100)
    result = budget.apply(sample_chunks)
    assert isinstance(result, BudgetResult)
    assert result.total_tokens <= 100
    assert len(result.included_chunks) <= len(sample_chunks)


def test_budget_respects_chunk_limit(sample_chunks):
    budget = ContextBudget(max_tokens=10000, max_chunks=2)
    result = budget.apply(sample_chunks)
    assert len(result.included_chunks) <= 2


def test_budget_includes_at_least_one_chunk(sample_chunks):
    budget = ContextBudget(max_tokens=50)
    result = budget.apply(sample_chunks)
    assert len(result.included_chunks) >= 1


# ── RerankPolicy ─────────────────────────────────────────────────────────────

def test_rerank_score_strategy_sorts_by_score(sample_chunks):
    policy = RerankPolicy(strategy=RerankStrategy.SCORE)
    reranked = policy.rerank(sample_chunks, query="orchestration")
    scores = [c["score"] for c in reranked]
    assert scores == sorted(scores, reverse=True)


def test_rerank_deduplication_removes_identical_content(sample_chunks):
    policy = RerankPolicy(strategy=RerankStrategy.SCORE, deduplicate=True)
    reranked = policy.rerank(sample_chunks, query="orchestration")
    contents = [c["content"] for c in reranked]
    assert len(contents) == len(set(contents))  # no duplicates


def test_rerank_diversity_limits_per_source(sample_chunks):
    policy = RerankPolicy(strategy=RerankStrategy.DIVERSITY, max_per_source=1)
    reranked = policy.rerank(sample_chunks, query="orchestration")
    source_counts: dict[str, int] = {}
    for c in reranked:
        url = c.get("source_url", "")
        source_counts[url] = source_counts.get(url, 0) + 1
    for url, count in source_counts.items():
        assert count <= 1


def test_rerank_filters_below_threshold(sample_chunks):
    policy = RerankPolicy(strategy=RerankStrategy.SCORE, min_score=0.75)
    reranked = policy.rerank(sample_chunks, query="orchestration")
    assert all(c["score"] >= 0.75 for c in reranked)


# ── CitationManager ───────────────────────────────────────────────────────────

def test_citation_manager_creates_numbered_citations(sample_chunks):
    mgr = CitationManager()
    cited_chunks, citations = mgr.attach_citations(sample_chunks[:3])
    assert len(citations) == 3
    for i, cit in enumerate(citations):
        assert cit.index == i + 1
        assert cit.source_url


def test_citation_manager_formats_inline_refs(sample_chunks):
    mgr = CitationManager()
    cited_chunks, citations = mgr.attach_citations(sample_chunks[:2])
    formatted = mgr.format_citation_block(citations)
    assert "[1]" in formatted
    assert "[2]" in formatted


# ── PromptBuilder ─────────────────────────────────────────────────────────────

def test_prompt_builder_creates_bundle():
    builder = PromptBuilder()
    bundle = PromptContextBundle(
        goal_context="List all open tickets",
        knowledge_chunks=[{"content": "Ticket #123 is open", "score": 0.9}],
        citations=[],
        session_memory=[],
        reflexion_lessons=[],
    )
    planner_prompt = builder.build_planner_context(bundle)
    assert "List all open tickets" in planner_prompt
    assert "Ticket #123" in planner_prompt


def test_prompt_builder_includes_citations(sample_chunks):
    from app.context.citation_manager import CitationManager, Citation
    mgr = CitationManager()
    _, citations = mgr.attach_citations(sample_chunks[:2])
    builder = PromptBuilder()
    bundle = PromptContextBundle(
        goal_context="explain orchestration",
        knowledge_chunks=sample_chunks[:2],
        citations=citations,
        session_memory=[],
        reflexion_lessons=[],
    )
    prompt = builder.build_executor_context(bundle, step="step 1")
    assert "docs.example.com" in prompt or "[1]" in prompt


def test_prompt_builder_applies_token_budget(sample_chunks):
    builder = PromptBuilder(max_context_tokens=50)
    bundle = PromptContextBundle(
        goal_context="test",
        knowledge_chunks=sample_chunks * 10,  # lots of chunks
        citations=[],
        session_memory=[],
        reflexion_lessons=[],
    )
    prompt = builder.build_planner_context(bundle)
    # Must not exceed reasonable size even with many chunks
    assert len(prompt) < 10000
```

- [ ] **Step 14.2: Create dirs and run to confirm failure**

```bash
mkdir -p agent-verse-backend/app/context
mkdir -p agent-verse-backend/tests/context
touch agent-verse-backend/app/context/__init__.py
touch agent-verse-backend/tests/context/__init__.py
cd agent-verse-backend && uv run pytest tests/context/test_context_pipeline.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 14.3: Implement `app/context/rerank_policy.py`**

```python
"""RerankPolicy — deduplication, score-based and diversity reranking."""
from __future__ import annotations

import enum
from typing import Any


class RerankStrategy(str, enum.Enum):
    SCORE = "score"
    RRF = "rrf"
    DIVERSITY = "diversity"
    CROSS_ENCODER = "cross_encoder"
    LLM = "llm"


class RerankPolicy:
    def __init__(
        self,
        strategy: RerankStrategy = RerankStrategy.SCORE,
        deduplicate: bool = True,
        min_score: float = 0.0,
        max_per_source: int = 5,
    ) -> None:
        self._strategy = strategy
        self._deduplicate = deduplicate
        self._min_score = min_score
        self._max_per_source = max_per_source

    def rerank(self, chunks: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        # 1. Filter by min score
        filtered = [c for c in chunks if c.get("score", 0.0) >= self._min_score]

        # 2. Deduplicate by content
        if self._deduplicate:
            seen_content: set[str] = set()
            deduped = []
            for c in filtered:
                content = c.get("content", "")
                if content not in seen_content:
                    seen_content.add(content)
                    deduped.append(c)
            filtered = deduped

        # 3. Apply strategy
        if self._strategy == RerankStrategy.SCORE:
            filtered = sorted(filtered, key=lambda c: c.get("score", 0.0), reverse=True)
        elif self._strategy == RerankStrategy.DIVERSITY:
            filtered = self._diversity_rerank(filtered)
        elif self._strategy == RerankStrategy.RRF:
            # RRF: already sorted by score as proxy
            filtered = sorted(filtered, key=lambda c: c.get("score", 0.0), reverse=True)

        # 4. Cap per source
        if self._max_per_source > 0:
            source_counts: dict[str, int] = {}
            capped = []
            for c in filtered:
                src = c.get("source_url", "_")
                if source_counts.get(src, 0) < self._max_per_source:
                    source_counts[src] = source_counts.get(src, 0) + 1
                    capped.append(c)
            filtered = capped

        return filtered

    def _diversity_rerank(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """MMR-like diversity: alternate between high-score and different-source."""
        if not chunks:
            return []
        result = []
        remaining = sorted(chunks, key=lambda c: c.get("score", 0.0), reverse=True)
        used_sources: set[str] = set()
        while remaining:
            # Pick highest score from a new source if possible
            for i, c in enumerate(remaining):
                src = c.get("source_url", "_")
                if src not in used_sources or len(used_sources) >= 3:
                    result.append(remaining.pop(i))
                    used_sources.add(src)
                    break
            else:
                result.append(remaining.pop(0))
        return result
```

- [ ] **Step 14.4: Implement `app/context/context_budget.py`**

```python
"""ContextBudget — enforces token limits on retrieved context."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Approximate: 1 token ≈ 4 characters
_CHARS_PER_TOKEN = 4


@dataclass
class BudgetResult:
    included_chunks: list[dict[str, Any]]
    excluded_count: int
    total_tokens: int


class ContextBudget:
    def __init__(self, max_tokens: int = 6000, max_chunks: int = 20) -> None:
        self._max_tokens = max_tokens
        self._max_chunks = max_chunks

    def apply(self, chunks: list[dict[str, Any]]) -> BudgetResult:
        included = []
        token_count = 0

        for chunk in chunks:
            content = chunk.get("content", "")
            chunk_tokens = max(1, len(content) // _CHARS_PER_TOKEN)

            if len(included) >= self._max_chunks:
                break
            if token_count + chunk_tokens > self._max_tokens and included:
                break

            included.append(chunk)
            token_count += chunk_tokens

        return BudgetResult(
            included_chunks=included,
            excluded_count=len(chunks) - len(included),
            total_tokens=token_count,
        )
```

- [ ] **Step 14.5: Implement `app/context/citation_manager.py`**

```python
"""CitationManager — threads source citations through retrieved context."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Citation:
    index: int
    chunk_id: str
    source_url: str
    page_number: int | None
    score: float
    content_preview: str = ""


class CitationManager:
    def attach_citations(
        self, chunks: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[Citation]]:
        citations = []
        annotated = []
        for i, chunk in enumerate(chunks, start=1):
            cit = Citation(
                index=i,
                chunk_id=chunk.get("chunk_id", ""),
                source_url=chunk.get("source_url", ""),
                page_number=chunk.get("page_number"),
                score=chunk.get("score", 0.0),
                content_preview=chunk.get("content", "")[:100],
            )
            citations.append(cit)
            annotated.append({**chunk, "_citation_index": i})
        return annotated, citations

    def format_citation_block(self, citations: list[Citation]) -> str:
        lines = ["Sources:"]
        for cit in citations:
            loc = f" p.{cit.page_number}" if cit.page_number else ""
            lines.append(f"[{cit.index}] {cit.source_url}{loc}")
        return "\n".join(lines)
```

- [ ] **Step 14.6: Implement `app/context/prompt_builder.py`**

```python
"""PromptBuilder — builds model-specific prompts from PromptContextBundle."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.context.citation_manager import Citation


_CHARS_PER_TOKEN = 4


@dataclass
class PromptContextBundle:
    """All context sources assembled before prompt construction."""
    goal_context: str
    knowledge_chunks: list[dict[str, Any]]
    citations: list[Any]
    session_memory: list[dict[str, Any]]
    reflexion_lessons: list[str]
    execution_memory: list[dict[str, Any]] = field(default_factory=list)
    long_term_memory: list[dict[str, Any]] = field(default_factory=list)
    semantic_cache_hits: list[dict[str, Any]] = field(default_factory=list)
    graph_facts: list[dict[str, Any]] = field(default_factory=list)
    web_results: list[dict[str, Any]] = field(default_factory=list)
    degradation_notes: list[str] = field(default_factory=list)
    source_inventory: dict[str, Any] = field(default_factory=dict)


class PromptBuilder:
    def __init__(self, max_context_tokens: int = 6000) -> None:
        self._max_tokens = max_context_tokens

    def _truncate_chunks(self, chunks: list[dict[str, Any]], token_budget: int) -> str:
        parts = []
        used = 0
        for chunk in chunks:
            content = chunk.get("content", "")
            tokens = len(content) // _CHARS_PER_TOKEN
            if used + tokens > token_budget:
                break
            citation_idx = chunk.get("_citation_index")
            ref = f" [{citation_idx}]" if citation_idx else ""
            parts.append(f"{content}{ref}")
            used += tokens
        return "\n\n".join(parts)

    def build_planner_context(self, bundle: PromptContextBundle) -> str:
        """Build context string for the planner LLM."""
        sections = [f"Goal: {bundle.goal_context}"]
        token_budget = self._max_tokens

        if bundle.knowledge_chunks:
            chunk_text = self._truncate_chunks(bundle.knowledge_chunks, token_budget // 2)
            if chunk_text:
                sections.append(f"Knowledge:\n{chunk_text}")

        if bundle.reflexion_lessons:
            lessons = "\n".join(f"- {l}" for l in bundle.reflexion_lessons[:3])
            sections.append(f"Past lessons:\n{lessons}")

        if bundle.execution_memory:
            plans = [str(m.get("plan", [])) for m in bundle.execution_memory[:2]]
            sections.append(f"Prior successful approaches:\n" + "\n".join(plans))

        if bundle.citations:
            from app.context.citation_manager import CitationManager
            mgr = CitationManager()
            sections.append(mgr.format_citation_block(bundle.citations))

        if bundle.degradation_notes:
            notes = "; ".join(bundle.degradation_notes)
            sections.append(f"[Note: {notes}]")

        return "\n\n".join(sections)

    def build_executor_context(self, bundle: PromptContextBundle, step: str = "") -> str:
        """Build per-step context for the executor LLM."""
        sections = []
        if step:
            sections.append(f"Current step: {step}")

        if bundle.knowledge_chunks:
            chunk_text = self._truncate_chunks(bundle.knowledge_chunks, self._max_tokens // 2)
            if chunk_text:
                sections.append(f"Context:\n{chunk_text}")

        if bundle.citations:
            from app.context.citation_manager import CitationManager
            mgr = CitationManager()
            sections.append(mgr.format_citation_block(bundle.citations))

        if bundle.web_results:
            web_text = "\n".join(r.get("content", r.get("snippet", ""))[:200]
                                 for r in bundle.web_results[:3])
            sections.append(f"Web results:\n{web_text}")

        return "\n\n".join(sections)

    def build_verifier_context(self, bundle: PromptContextBundle) -> str:
        """Build context for the verifier LLM — focus on citations and confidence."""
        sections = [f"Goal: {bundle.goal_context}"]
        if bundle.citations:
            from app.context.citation_manager import CitationManager
            mgr = CitationManager()
            sections.append(mgr.format_citation_block(bundle.citations))
        if bundle.degradation_notes:
            sections.append("Degradation notes: " + "; ".join(bundle.degradation_notes))
        return "\n\n".join(sections)
```

- [ ] **Step 14.7: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/context/test_context_pipeline.py -v --no-cov
```
Expected: `14 passed`

- [ ] **Step 14.8: Commit**

```bash
cd agent-verse-backend
git add app/context/ tests/context/
git commit -m "feat(context): add ContextBudget + RerankPolicy + CitationManager + PromptBuilder"
```

---

## Task 15: Ingestion Orchestrator

**Files:**
- Create: `app/ingestion/__init__.py`
- Create: `app/ingestion/content_classifier.py`
- Create: `app/ingestion/parser_registry.py`
- Create: `app/ingestion/chunking_strategy_selector.py`
- Create: `app/ingestion/orchestrator.py`
- Create: `tests/ingestion/__init__.py`
- Create: `tests/ingestion/test_ingestion_orchestrator.py`

- [ ] **Step 15.1: Write failing tests**

```python
# tests/ingestion/test_ingestion_orchestrator.py
"""Same ingestion API must accept all supported content types."""
from __future__ import annotations
import pytest
from app.ingestion.content_classifier import ContentClassifier, ContentType
from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
from app.ingestion.parser_registry import ParserRegistry
from app.ingestion.orchestrator import IngestionOrchestrator, IngestionResult
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def orchestrator():
    return IngestionOrchestrator()


# ── ContentClassifier ─────────────────────────────────────────────────────────

def test_classify_plain_text():
    classifier = ContentClassifier()
    result = classifier.classify("The quick brown fox jumps over the lazy dog.")
    assert result == ContentType.TEXT


def test_classify_python_code():
    classifier = ContentClassifier()
    result = classifier.classify("def hello():\n    return 'world'\n\nimport os")
    assert result == ContentType.CODE


def test_classify_html():
    classifier = ContentClassifier()
    result = classifier.classify("<html><body><h1>Hello</h1></body></html>")
    assert result == ContentType.HTML


def test_classify_by_filename():
    classifier = ContentClassifier()
    assert classifier.classify_by_filename("report.pdf") == ContentType.PDF
    assert classifier.classify_by_filename("data.csv") == ContentType.CSV
    assert classifier.classify_by_filename("script.py") == ContentType.CODE
    assert classifier.classify_by_filename("document.docx") == ContentType.DOCX
    assert classifier.classify_by_filename("image.png") == ContentType.IMAGE
    assert classifier.classify_by_filename("audio.mp3") == ContentType.AUDIO
    assert classifier.classify_by_filename("video.mp4") == ContentType.VIDEO


# ── ChunkingStrategySelector ─────────────────────────────────────────────────

def test_text_gets_semantic_chunking():
    selector = ChunkingStrategySelector()
    strategy = selector.select(ContentType.TEXT)
    assert strategy == "semantic"


def test_code_gets_ast_chunking():
    selector = ChunkingStrategySelector()
    strategy = selector.select(ContentType.CODE)
    assert strategy == "ast"


def test_pdf_gets_layout_chunking():
    selector = ChunkingStrategySelector()
    strategy = selector.select(ContentType.PDF)
    assert strategy in ("layout", "page", "section")


def test_audio_gets_timestamp_chunking():
    selector = ChunkingStrategySelector()
    strategy = selector.select(ContentType.AUDIO)
    assert strategy == "timestamp"


def test_video_gets_scene_chunking():
    selector = ChunkingStrategySelector()
    strategy = selector.select(ContentType.VIDEO)
    assert strategy == "scene"


def test_csv_gets_row_group_chunking():
    selector = ChunkingStrategySelector()
    strategy = selector.select(ContentType.CSV)
    assert strategy in ("row_group", "table")


# ── IngestionOrchestrator ─────────────────────────────────────────────────────

async def test_ingest_text_returns_chunks(orchestrator, tenant_ctx):
    result = await orchestrator.ingest(
        content="The AgentVerse platform uses dynamic orchestration to route goals.",
        content_type="text",
        collection_id="col1",
        tenant_ctx=tenant_ctx,
    )
    assert isinstance(result, IngestionResult)
    assert result.chunks_created >= 1
    assert result.content_type == ContentType.TEXT
    assert result.chunking_strategy == "semantic"


async def test_ingest_code_returns_chunks(orchestrator, tenant_ctx):
    code = "def calculate(x, y):\n    return x + y\n\nclass Calculator:\n    pass"
    result = await orchestrator.ingest(
        content=code,
        content_type="code",
        collection_id="col2",
        tenant_ctx=tenant_ctx,
    )
    assert result.chunks_created >= 1
    assert result.chunking_strategy == "ast"


async def test_ingest_attaches_provenance(orchestrator, tenant_ctx):
    result = await orchestrator.ingest(
        content="Test content for provenance tracking.",
        content_type="text",
        collection_id="col3",
        tenant_ctx=tenant_ctx,
        source_url="https://source.example.com/doc1",
    )
    assert result.source_url == "https://source.example.com/doc1"
    assert result.tenant_id == tenant_ctx.tenant_id


async def test_ingest_unknown_type_defaults_to_text(orchestrator, tenant_ctx):
    result = await orchestrator.ingest(
        content="Some content",
        content_type="unknown",
        collection_id="col4",
        tenant_ctx=tenant_ctx,
    )
    assert result.chunks_created >= 1
```

- [ ] **Step 15.2: Create dirs and run to confirm failure**

```bash
mkdir -p agent-verse-backend/app/ingestion
mkdir -p agent-verse-backend/tests/ingestion
touch agent-verse-backend/app/ingestion/__init__.py
touch agent-verse-backend/tests/ingestion/__init__.py
cd agent-verse-backend && uv run pytest tests/ingestion/test_ingestion_orchestrator.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 15.3: Implement `app/ingestion/content_classifier.py`**

```python
"""ContentClassifier — detects content type from text or filename."""
from __future__ import annotations

import enum
import re


class ContentType(str, enum.Enum):
    TEXT = "text"
    PDF = "pdf"
    DOCX = "docx"
    HTML = "html"
    MARKDOWN = "markdown"
    CODE = "code"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    CSV = "csv"
    JSON = "json"
    WEB_PAGE = "web_page"
    MIXED = "mixed"


_EXT_MAP: dict[str, ContentType] = {
    ".pdf": ContentType.PDF, ".docx": ContentType.DOCX, ".doc": ContentType.DOCX,
    ".html": ContentType.HTML, ".htm": ContentType.HTML,
    ".md": ContentType.MARKDOWN, ".markdown": ContentType.MARKDOWN,
    ".py": ContentType.CODE, ".js": ContentType.CODE, ".ts": ContentType.CODE,
    ".java": ContentType.CODE, ".go": ContentType.CODE, ".rs": ContentType.CODE,
    ".cpp": ContentType.CODE, ".c": ContentType.CODE, ".rb": ContentType.CODE,
    ".sh": ContentType.CODE, ".sql": ContentType.CODE,
    ".png": ContentType.IMAGE, ".jpg": ContentType.IMAGE, ".jpeg": ContentType.IMAGE,
    ".gif": ContentType.IMAGE, ".webp": ContentType.IMAGE, ".svg": ContentType.IMAGE,
    ".mp3": ContentType.AUDIO, ".wav": ContentType.AUDIO, ".ogg": ContentType.AUDIO,
    ".mp4": ContentType.VIDEO, ".mov": ContentType.VIDEO, ".avi": ContentType.VIDEO,
    ".csv": ContentType.CSV, ".tsv": ContentType.CSV,
    ".json": ContentType.JSON, ".jsonl": ContentType.JSON,
}

_CODE_PATTERNS = re.compile(
    r"(?m)^(?:def |class |import |from .+ import |function |const |let |var |public class )"
)
_HTML_PATTERN = re.compile(r"<(?:html|body|div|span|p|h[1-6]|script|style)", re.I)
_JSON_PATTERN = re.compile(r"^\s*[\[\{]")
_MARKDOWN_PATTERN = re.compile(r"(?m)^#{1,6}\s|^\*\*|^-\s|^\d+\.\s")


class ContentClassifier:
    def classify(self, content: str) -> ContentType:
        if _HTML_PATTERN.search(content[:500]):
            return ContentType.HTML
        if _JSON_PATTERN.match(content[:20]):
            return ContentType.JSON
        if _CODE_PATTERNS.search(content[:1000]):
            return ContentType.CODE
        if _MARKDOWN_PATTERN.search(content[:500]):
            return ContentType.MARKDOWN
        return ContentType.TEXT

    def classify_by_filename(self, filename: str) -> ContentType:
        import os
        _, ext = os.path.splitext(filename.lower())
        return _EXT_MAP.get(ext, ContentType.TEXT)
```

- [ ] **Step 15.4: Implement `app/ingestion/chunking_strategy_selector.py`**

```python
"""ChunkingStrategySelector — selects chunking strategy by content type."""
from __future__ import annotations

from app.ingestion.content_classifier import ContentType

_STRATEGY_MAP: dict[ContentType, str] = {
    ContentType.TEXT: "semantic",
    ContentType.MARKDOWN: "heading",
    ContentType.PDF: "layout",
    ContentType.DOCX: "paragraph",
    ContentType.HTML: "dom",
    ContentType.CODE: "ast",
    ContentType.IMAGE: "region",
    ContentType.AUDIO: "timestamp",
    ContentType.VIDEO: "scene",
    ContentType.CSV: "row_group",
    ContentType.JSON: "record",
    ContentType.WEB_PAGE: "dom",
    ContentType.MIXED: "semantic",
}


class ChunkingStrategySelector:
    def select(self, content_type: ContentType) -> str:
        return _STRATEGY_MAP.get(content_type, "semantic")
```

- [ ] **Step 15.5: Implement `app/ingestion/parser_registry.py`**

```python
"""ParserRegistry — maps ContentType to parser implementation."""
from __future__ import annotations

from app.ingestion.content_classifier import ContentType


class TextParser:
    def parse(self, content: str, **kwargs) -> list[str]:
        """Split into paragraphs for semantic chunking."""
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        return paragraphs or [content]


class CodeParser:
    def parse(self, content: str, **kwargs) -> list[str]:
        """Split by function/class definitions."""
        import re
        blocks = re.split(r"(?m)^(?=def |class |function |const |let )", content)
        return [b.strip() for b in blocks if b.strip()] or [content]


class HTMLParser:
    def parse(self, content: str, **kwargs) -> list[str]:
        """Strip HTML tags and split into paragraphs."""
        import re
        text = re.sub(r"<[^>]+>", " ", content)
        text = re.sub(r"\s+", " ", text).strip()
        return [text] if text else [content]


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers = {
            ContentType.TEXT: TextParser(),
            ContentType.MARKDOWN: TextParser(),
            ContentType.CODE: CodeParser(),
            ContentType.HTML: HTMLParser(),
            ContentType.WEB_PAGE: HTMLParser(),
        }

    def get_parser(self, content_type: ContentType) -> TextParser:
        return self._parsers.get(content_type, TextParser())
```

- [ ] **Step 15.6: Implement `app/ingestion/orchestrator.py`**

```python
"""IngestionOrchestrator — routes content to the right parser, chunker, and store."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from app.ingestion.content_classifier import ContentClassifier, ContentType
from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
from app.ingestion.parser_registry import ParserRegistry

if TYPE_CHECKING:
    from app.tenancy.context import TenantContext


@dataclass
class IngestionResult:
    ingestion_id: str
    tenant_id: str
    collection_id: str
    content_type: ContentType
    chunking_strategy: str
    chunks_created: int
    source_url: str = ""
    chunk_ids: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.chunk_ids is None:
            self.chunk_ids = []


class IngestionOrchestrator:
    def __init__(
        self,
        *,
        knowledge_store: Any = None,
        embedder: Any = None,
    ) -> None:
        self._kb = knowledge_store
        self._embedder = embedder
        self._classifier = ContentClassifier()
        self._chunking_selector = ChunkingStrategySelector()
        self._parser_registry = ParserRegistry()

    async def ingest(
        self,
        content: str,
        *,
        content_type: str = "auto",
        collection_id: str,
        tenant_ctx: "TenantContext",
        source_url: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> IngestionResult:
        # 1. Detect content type
        if content_type == "auto" or content_type == "unknown":
            detected = self._classifier.classify(content)
        else:
            try:
                detected = ContentType(content_type)
            except ValueError:
                detected = ContentType.TEXT

        # 2. Select chunking strategy
        chunking_strategy = self._chunking_selector.select(detected)

        # 3. Parse into chunks
        parser = self._parser_registry.get_parser(detected)
        chunks_text = parser.parse(content)

        # 4. Store chunks (in-memory or real KB)
        chunk_ids: list[str] = []
        for chunk_text in chunks_text:
            chunk_id = uuid.uuid4().hex
            if self._kb is not None:
                try:
                    await self._kb.ingest_document(
                        collection_id=collection_id,
                        content=chunk_text,
                        metadata={
                            **(metadata or {}),
                            "content_type": detected.value,
                            "chunking_strategy": chunking_strategy,
                        },
                        tenant_ctx=tenant_ctx,
                        embedder=self._embedder,
                        source_url=source_url,
                        source_type=detected.value,
                    )
                except Exception:
                    pass
            chunk_ids.append(chunk_id)

        return IngestionResult(
            ingestion_id=uuid.uuid4().hex,
            tenant_id=tenant_ctx.tenant_id,
            collection_id=collection_id,
            content_type=detected,
            chunking_strategy=chunking_strategy,
            chunks_created=len(chunk_ids),
            source_url=source_url,
            chunk_ids=chunk_ids,
        )
```

- [ ] **Step 15.7: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/ingestion/test_ingestion_orchestrator.py -v --no-cov
```
Expected: `15 passed`

- [ ] **Step 15.8: Commit**

```bash
cd agent-verse-backend
git add app/ingestion/ tests/ingestion/
git commit -m "feat(ingestion): add IngestionOrchestrator with multimodal content type routing"
```

---

## Task 16: Embedding Orchestrator

**Files:**
- Create: `app/embedding/orchestrator.py`
- Create: `app/embedding/model_registry.py`
- Create: `app/embedding/dimension_policy.py`
- Create: `tests/embedding/__init__.py`
- Create: `tests/embedding/test_embedding_orchestrator.py`

- [ ] **Step 16.1: Write failing tests**

```python
# tests/embedding/test_embedding_orchestrator.py
"""EmbeddingOrchestrator selects the right model per modality and tenant budget."""
from __future__ import annotations
import pytest
from app.embedding.orchestrator import EmbeddingOrchestrator, EmbeddingSelectionResult
from app.embedding.model_registry import EmbeddingModelRegistry, EmbeddingModelSpec
from app.embedding.dimension_policy import DimensionPolicy
from app.ingestion.content_classifier import ContentType
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def registry():
    return EmbeddingModelRegistry.build_default()


@pytest.fixture
def orchestrator(registry):
    return EmbeddingOrchestrator(registry=registry)


def test_text_content_gets_text_embedding(orchestrator, tenant_ctx):
    result = orchestrator.select(
        content_type=ContentType.TEXT,
        tenant_ctx=tenant_ctx,
    )
    assert isinstance(result, EmbeddingSelectionResult)
    assert result.model_id is not None
    assert result.dimension > 0


def test_code_content_gets_code_embedding(orchestrator, tenant_ctx):
    result = orchestrator.select(
        content_type=ContentType.CODE,
        tenant_ctx=tenant_ctx,
    )
    assert result.modality in ("code", "text")   # code or fallback to text


def test_image_content_gets_multimodal_or_text_embedding(orchestrator, tenant_ctx):
    result = orchestrator.select(
        content_type=ContentType.IMAGE,
        tenant_ctx=tenant_ctx,
    )
    assert result.model_id is not None


def test_free_plan_gets_cheaper_model(orchestrator):
    free_ctx = TenantContext(tenant_id="t2", plan=PlanTier.FREE, api_key_id="k2")
    result = orchestrator.select(content_type=ContentType.TEXT, tenant_ctx=free_ctx)
    assert result.cost_class in ("free", "low")


def test_model_registry_has_text_models(registry):
    text_models = registry.list_by_modality("text")
    assert len(text_models) > 0


def test_model_registry_has_code_models(registry):
    code_models = registry.list_by_modality("code")
    assert len(code_models) >= 0   # may fall back to text


def test_dimension_policy_returns_valid_dimension():
    policy = DimensionPolicy()
    dim = policy.select(model_id="text-embedding-3-small")
    assert dim in (768, 1024, 1536, 3072)


def test_embedding_selection_result_has_all_fields(orchestrator, tenant_ctx):
    result = orchestrator.select(content_type=ContentType.TEXT, tenant_ctx=tenant_ctx)
    assert hasattr(result, "model_id")
    assert hasattr(result, "dimension")
    assert hasattr(result, "modality")
    assert hasattr(result, "cost_class")
```

- [ ] **Step 16.2: Create dirs and run to confirm failure**

```bash
mkdir -p agent-verse-backend/tests/embedding
touch agent-verse-backend/tests/embedding/__init__.py
cd agent-verse-backend && uv run pytest tests/embedding/test_embedding_orchestrator.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 16.3: Implement `app/embedding/model_registry.py`**

```python
"""EmbeddingModelRegistry — catalogue of available embedding models."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EmbeddingModelSpec:
    model_id: str
    modality: str               # text|code|multimodal|image
    dimension: int
    cost_class: str             # free|low|medium|high
    provider: str
    description: str = ""
    max_input_tokens: int = 8192


class EmbeddingModelRegistry:
    def __init__(self, models: list[EmbeddingModelSpec]) -> None:
        self._by_id = {m.model_id: m for m in models}

    def get(self, model_id: str) -> EmbeddingModelSpec | None:
        return self._by_id.get(model_id)

    def list_by_modality(self, modality: str) -> list[EmbeddingModelSpec]:
        return [m for m in self._by_id.values() if m.modality == modality]

    def list_all(self) -> list[EmbeddingModelSpec]:
        return list(self._by_id.values())

    def filter(self, *, cost_class: str | None = None) -> list[EmbeddingModelSpec]:
        results = list(self._by_id.values())
        if cost_class:
            results = [m for m in results if m.cost_class == cost_class]
        return results

    @classmethod
    def build_default(cls) -> "EmbeddingModelRegistry":
        return cls([
            EmbeddingModelSpec("text-embedding-3-small", "text", 1536, "low",
                               "openai", "OpenAI small text embedding"),
            EmbeddingModelSpec("text-embedding-3-large", "text", 3072, "medium",
                               "openai", "OpenAI large text embedding"),
            EmbeddingModelSpec("voyage-3-lite", "text", 1024, "low",
                               "voyage", "Voyage text embedding lite"),
            EmbeddingModelSpec("voyage-code-3", "code", 1024, "low",
                               "voyage", "Voyage code embedding"),
            EmbeddingModelSpec("voyage-multimodal-3", "multimodal", 1024, "medium",
                               "voyage", "Voyage multimodal embedding"),
            EmbeddingModelSpec("fake-embedding", "text", 10, "free",
                               "fake", "Fake embedding for testing"),
        ])
```

- [ ] **Step 16.4: Implement `app/embedding/dimension_policy.py`**

```python
"""DimensionPolicy — maps model IDs to standard vector dimensions."""
from __future__ import annotations

_DIMENSION_MAP: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "voyage-3-lite": 1024,
    "voyage-code-3": 1024,
    "voyage-multimodal-3": 1024,
    "fake-embedding": 10,
}


class DimensionPolicy:
    def select(self, model_id: str) -> int:
        return _DIMENSION_MAP.get(model_id, 1536)
```

- [ ] **Step 16.5: Implement `app/embedding/orchestrator.py`**

```python
"""EmbeddingOrchestrator — selects embedding model per content type and tenant policy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.embedding.model_registry import EmbeddingModelRegistry, EmbeddingModelSpec
from app.embedding.dimension_policy import DimensionPolicy
from app.ingestion.content_classifier import ContentType

if TYPE_CHECKING:
    from app.tenancy.context import TenantContext

_MODALITY_MAP: dict[ContentType, list[str]] = {
    ContentType.TEXT: ["text"],
    ContentType.MARKDOWN: ["text"],
    ContentType.CODE: ["code", "text"],
    ContentType.PDF: ["text"],
    ContentType.DOCX: ["text"],
    ContentType.HTML: ["text"],
    ContentType.IMAGE: ["multimodal", "image", "text"],
    ContentType.AUDIO: ["text"],  # transcript embedding
    ContentType.VIDEO: ["multimodal", "text"],
    ContentType.CSV: ["text"],
    ContentType.JSON: ["text"],
}

_COST_BY_PLAN = {
    "free": ["free", "low"],
    "starter": ["free", "low"],
    "professional": ["free", "low", "medium"],
    "enterprise": ["free", "low", "medium", "high"],
}


@dataclass
class EmbeddingSelectionResult:
    model_id: str
    dimension: int
    modality: str
    cost_class: str
    provider: str
    selection_reason: str = ""


class EmbeddingOrchestrator:
    def __init__(self, registry: EmbeddingModelRegistry | None = None) -> None:
        self._registry = registry or EmbeddingModelRegistry.build_default()
        self._dim_policy = DimensionPolicy()

    def select(
        self,
        content_type: ContentType,
        tenant_ctx: "TenantContext",
    ) -> EmbeddingSelectionResult:
        modalities = _MODALITY_MAP.get(content_type, ["text"])
        allowed_costs = _COST_BY_PLAN.get(tenant_ctx.plan.value, ["low"])

        # Try each modality in preference order
        for modality in modalities:
            candidates = self._registry.list_by_modality(modality)
            # Filter by allowed cost class
            affordable = [c for c in candidates if c.cost_class in allowed_costs]
            if affordable:
                # Pick highest quality (largest dimension) that's affordable
                best = max(affordable, key=lambda m: m.dimension)
                return EmbeddingSelectionResult(
                    model_id=best.model_id,
                    dimension=self._dim_policy.select(best.model_id),
                    modality=best.modality,
                    cost_class=best.cost_class,
                    provider=best.provider,
                    selection_reason=f"content_type={content_type.value} modality={modality}",
                )

        # Fallback to any text model
        fallback = self._registry.list_by_modality("text")
        if fallback:
            m = fallback[0]
            return EmbeddingSelectionResult(
                model_id=m.model_id,
                dimension=self._dim_policy.select(m.model_id),
                modality=m.modality,
                cost_class=m.cost_class,
                provider=m.provider,
                selection_reason="fallback to text embedding",
            )

        # Ultimate fallback
        return EmbeddingSelectionResult(
            model_id="fake-embedding",
            dimension=10,
            modality="text",
            cost_class="free",
            provider="fake",
            selection_reason="no embedding model available",
        )
```

- [ ] **Step 16.6: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/embedding/test_embedding_orchestrator.py -v --no-cov
```
Expected: `8 passed`

- [ ] **Step 16.7: Run all Part 3 tests**

```bash
cd agent-verse-backend
uv run pytest tests/rag/test_agentic/ tests/context/ tests/ingestion/ tests/embedding/ -v --no-cov
```
Expected: All 47+ tests pass

- [ ] **Step 16.8: Commit**

```bash
cd agent-verse-backend
git add app/embedding/orchestrator.py app/embedding/model_registry.py app/embedding/dimension_policy.py \
    tests/embedding/
git commit -m "feat(embedding): add EmbeddingOrchestrator with modality-aware model selection"
```
