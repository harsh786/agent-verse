# AgentVerse Agentic RAG — Implementation Plan

> **For agentic workers:** Use subagent-driven-development to execute tasks in parallel.

**Goal:** Transform AgentVerse from pre-baked RAG into a fully agentic RAG system where the agent drives retrieval as an explicit tool, degradation is transparent, and all available sources are used.

**Architecture reference:** `docs/architecture/2026-07-07-agentverse-agentic-rag-design.md`

**Tech Stack:** Python 3.12, FastAPI, LangGraph, pgvector, Redis, SearxNG

---

## Phase A: Foundation (Highest Impact — Do First)

### Task A1: Create `RetrieverTool` — unified retrieval interface

**Files:**
- Create: `app/rag/retriever_tool.py`
- Modify: `app/agent/graph.py` (import only)

- [ ] **Step 1: Write tests first**

```python
# tests/rag/test_retriever_tool.py
import asyncio, pytest
from app.rag.retriever_tool import RetrieverTool, RetrievalResult
from app.tenancy.context import TenantContext, PlanTier

CTX = TenantContext(tenant_id="rt-test", plan=PlanTier.FREE, api_key_id="k")

def test_retriever_tool_no_sources_returns_parametric():
    """With no sources wired, result must be parametric baseline — never empty."""
    tool = RetrieverTool()
    result = asyncio.run(tool.retrieve("test query", tenant_ctx=CTX))
    assert result.source in ("parametric", "none_available")
    assert result.confidence == 0.0
    assert result.context != ""  # must be informative, not silent empty

def test_retriever_tool_reports_source_inventory():
    tool = RetrieverTool()
    inventory = asyncio.run(tool.build_source_inventory(CTX))
    assert "kb_collections" in inventory
    assert "web_available" in inventory
    assert "embedder_available" in inventory

def test_retriever_tool_low_confidence_flags_correctly():
    tool = RetrieverTool()
    result = asyncio.run(tool.retrieve("test query", tenant_ctx=CTX, min_confidence=0.8))
    assert result.confidence <= result.min_confidence_threshold or result.fallback_used
```

- [ ] **Step 2: Run tests — confirm they FAIL**

```bash
cd agent-verse-backend && uv run pytest tests/rag/test_retriever_tool.py -v --no-cov 2>&1 | tail -10
```
Expected: `ImportError` (module doesn't exist yet)

- [ ] **Step 3: Implement `app/rag/retriever_tool.py`**

```python
"""
RetrieverTool — unified retrieval interface for agentic RAG.

The agent calls this directly as a tool. It handles:
- Strategy selection (auto/vector/graph/hyde/web/memory/hybrid)
- Multi-source parallel retrieval
- Confidence-gated web fallback
- Query reformulation on miss
- Graceful degradation (never returns silent empty)
"""
from __future__ import annotations
import asyncio, time
from dataclasses import dataclass, field
from typing import Any
from app.tenancy.context import TenantContext
from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Citation:
    index: int
    content: str
    score: float
    source: str          # kb / web / kg / memory / parametric
    collection_id: str = ""
    url: str = ""
    chunk_id: str = ""


@dataclass
class RetrievalResult:
    query: str
    context: str              # formatted text ready for prompt injection
    citations: list[Citation] = field(default_factory=list)
    confidence: float = 0.0   # 0.0–1.0; 0.0 = parametric baseline
    source: str = "none"      # kb / web / kg / memory / hybrid / parametric
    strategy_used: str = "none"
    fallback_used: bool = False
    fallback_chain: list[str] = field(default_factory=list)
    reformulation_attempts: int = 0
    latency_ms: float = 0.0
    min_confidence_threshold: float = 0.3
    empty_reason: str = ""    # why context is empty/parametric


_PARAMETRIC_CONTEXT = (
    "No external context is available from any retrieval source.\n"
    "The model is operating on parametric (training) knowledge only.\n"
    "Mark outputs with low confidence. The verifier will flag unsupported claims.\n"
    "If this step requires current or domain-specific information, indicate this clearly."
)

_CONTEXT_GAP_SIGNALS = frozenset({
    "insufficient", "unclear", "no information", "cannot determine",
    "lack of context", "not mentioned", "unknown", "not found",
    "need more", "more context", "cannot verify", "no evidence",
})


class RetrieverTool:
    """Unified retrieval tool for agentic RAG. Wire dependencies via set_dependencies()."""

    name = "retrieve_context"
    description = (
        "Retrieve relevant context from knowledge base, web, knowledge graph, "
        "or memory. Specify strategy: auto | kb | web | graph | memory | hybrid"
    )

    def __init__(self) -> None:
        self._knowledge_store: Any = None
        self._kg_store: Any = None
        self._exec_memory: Any = None
        self._long_term_memory: Any = None
        self._web_search: Any = None
        self._embedder: Any = None
        self._provider: Any = None
        self._db: Any = None

    def set_dependencies(
        self,
        knowledge_store: Any = None,
        kg_store: Any = None,
        exec_memory: Any = None,
        long_term_memory: Any = None,
        web_search: Any = None,
        embedder: Any = None,
        provider: Any = None,
        db: Any = None,
    ) -> None:
        self._knowledge_store = knowledge_store
        self._kg_store = kg_store
        self._exec_memory = exec_memory
        self._long_term_memory = long_term_memory
        self._web_search = web_search
        self._embedder = embedder
        self._provider = provider
        self._db = db

    async def build_source_inventory(self, tenant_ctx: TenantContext) -> dict:
        """Report what retrieval sources are available for this tenant."""
        import os
        inventory: dict = {
            "kb_collections": 0,
            "kb_total_chunks": 0,
            "kg_nodes": 0,
            "ltm_entries": 0,
            "exec_memory_plans": 0,
            "web_available": bool(
                os.getenv("SEARXNG_URL") or os.getenv("DUCKDUCKGO_FALLBACK", "true") == "true"
            ),
            "embedder_available": self._embedder is not None,
        }
        if self._knowledge_store:
            try:
                cols = self._knowledge_store.list_collections(tenant_ctx=tenant_ctx)
                inventory["kb_collections"] = len(cols)
            except Exception:
                pass
        if self._kg_store:
            try:
                nodes = self._kg_store.query_nodes(tenant_ctx.tenant_id, limit=1)
                inventory["kg_nodes"] = 1 if nodes else 0
            except Exception:
                pass
        return inventory

    async def retrieve(
        self,
        query: str,
        *,
        tenant_ctx: TenantContext,
        strategy: str = "auto",
        collection_id: str | None = None,
        top_k: int = 5,
        min_confidence: float = 0.3,
        step_context: str = "",
        allow_web_fallback: bool = True,
        allow_reformulation: bool = True,
        max_reformulation_attempts: int = 2,
    ) -> RetrievalResult:
        """Main retrieval entry point. Never returns silent empty context."""
        start = time.monotonic()
        fallback_chain: list[str] = []
        all_chunks: list[dict] = []
        source = "none"

        inventory = await self.build_source_inventory(tenant_ctx)

        # 1. Embed query (best-effort — lexical fallback if unavailable)
        query_embedding: list[float] | None = None
        if self._embedder:
            try:
                from app.providers.base import EmbedRequest
                resp = await self._embedder.embed(EmbedRequest(texts=[query]))
                query_embedding = resp.embeddings[0] if resp.embeddings else None
            except Exception:
                pass

        # 2. Auto strategy selection
        if strategy == "auto":
            strategy = self._select_strategy(query, inventory)

        # 3. Primary retrieval
        if strategy in ("kb", "hybrid", "auto", "vector", "lexical") and self._knowledge_store:
            chunks = await self._search_kb(query, query_embedding, collection_id, tenant_ctx, top_k)
            if chunks:
                all_chunks.extend(chunks)
                source = "kb"
                fallback_chain.append("kb")

        if strategy in ("graph", "hybrid") and self._kg_store:
            chunks = await self._search_kg(query, tenant_ctx)
            if chunks:
                all_chunks.extend(chunks)
                source = "kg" if source == "none" else "hybrid"
                fallback_chain.append("kg")

        if strategy == "memory":
            chunks = await self._search_memory(query, tenant_ctx)
            if chunks:
                all_chunks.extend(chunks)
                source = "memory"
                fallback_chain.append("memory")

        # 4. Query reformulation on miss
        reformulation_attempts = 0
        if not all_chunks and allow_reformulation and self._knowledge_store:
            for attempt in range(max_reformulation_attempts):
                reformulated = self._reformulate_query(query, attempt)
                chunks = await self._search_kb(
                    reformulated, query_embedding, collection_id, tenant_ctx, top_k
                )
                if chunks:
                    all_chunks.extend(chunks)
                    source = "kb"
                    fallback_chain.append(f"kb_reformulated_{attempt+1}")
                    break
                reformulation_attempts += 1

        # 5. Confidence check → web fallback
        avg_confidence = (
            sum(c.get("score", 0) for c in all_chunks) / len(all_chunks)
            if all_chunks else 0.0
        )
        fallback_used = False
        if (
            allow_web_fallback
            and inventory["web_available"]
            and (not all_chunks or avg_confidence < min_confidence)
        ):
            web_chunks = await self._search_web(query, tenant_ctx)
            if web_chunks:
                all_chunks.extend(web_chunks)
                fallback_used = True
                source = "web" if source == "none" else "hybrid"
                fallback_chain.append("web")

        # 6. Memory fallback (always try if other sources empty)
        if not all_chunks and self._exec_memory:
            memory_chunks = await self._search_memory(query, tenant_ctx)
            if memory_chunks:
                all_chunks.extend(memory_chunks)
                source = "memory"
                fallback_chain.append("memory_fallback")

        # 7. Rerank if multiple chunks
        if len(all_chunks) > top_k:
            try:
                from app.rag_platform.reranker import reranker
                if self._provider:
                    reranker.set_provider(self._provider)
                all_chunks = await reranker.rerank(query, all_chunks, top_k)
            except Exception:
                all_chunks = sorted(all_chunks, key=lambda c: c.get("score", 0), reverse=True)[:top_k]

        # 8. Build result
        final_confidence = (
            sum(c.get("score", 0) for c in all_chunks) / max(len(all_chunks), 1)
            if all_chunks else 0.0
        )

        if all_chunks:
            context_lines = []
            citations = []
            for i, chunk in enumerate(all_chunks[:top_k]):
                content = chunk.get("content", "")[:400]
                score = chunk.get("score", 0.0)
                src = chunk.get("source", source)
                context_lines.append(f"[{i+1}] (source={src}, score={score:.2f})\n{content}")
                citations.append(Citation(
                    index=i+1, content=content, score=score, source=src,
                    collection_id=chunk.get("collection_id", ""),
                    url=chunk.get("url", ""),
                    chunk_id=chunk.get("chunk_id", ""),
                ))
            context = "\n\n".join(context_lines)
        else:
            # Parametric baseline — informative, never silent
            context = _PARAMETRIC_CONTEXT
            if inventory["kb_collections"] == 0:
                context = (
                    f"[No knowledge base configured — {inventory['kb_collections']} collections]\n"
                    + _PARAMETRIC_CONTEXT
                )
            source = "parametric"
            citations = []
            final_confidence = 0.0

        return RetrievalResult(
            query=query,
            context=context,
            citations=citations,
            confidence=final_confidence,
            source=source,
            strategy_used=strategy,
            fallback_used=fallback_used,
            fallback_chain=fallback_chain,
            reformulation_attempts=reformulation_attempts,
            latency_ms=(time.monotonic() - start) * 1000,
            min_confidence_threshold=min_confidence,
        )

    def _select_strategy(self, query: str, inventory: dict) -> str:
        q = query.lower()
        if any(kw in q for kw in ("related to", "connected to", "depends on", "leads to")):
            return "graph"
        if any(kw in q for kw in ("how does", "why did", "what caused", "explain step")):
            return "hybrid"
        if inventory["kb_collections"] == 0 and inventory["web_available"]:
            return "web"
        return "hybrid"

    def _reformulate_query(self, query: str, attempt: int) -> str:
        words = query.split()
        if attempt == 0:
            # Broader: use first 3 key words
            return " ".join(words[:3]) if len(words) > 3 else query
        else:
            # Narrower: add "overview" or "definition"
            return f"{query} overview definition"

    async def _search_kb(
        self, query: str, embedding: list[float] | None,
        collection_id: str | None, tenant_ctx: TenantContext, top_k: int
    ) -> list[dict]:
        if not self._knowledge_store:
            return []
        try:
            cols = (
                [type("C", (), {"collection_id": collection_id})]
                if collection_id
                else self._knowledge_store.list_collections(tenant_ctx=tenant_ctx)[:3]
            )
            results = []
            for col in cols:
                cid = col.collection_id if hasattr(col, "collection_id") else col
                hits = await self._knowledge_store.hybrid_search_db(
                    query=query, query_embedding=embedding or [],
                    collection_id=cid, tenant_ctx=tenant_ctx, top_k=top_k,
                )
                for h in hits:
                    results.append({
                        "content": getattr(h, "content", str(h)),
                        "score": getattr(h, "score", 0.5),
                        "source": "kb",
                        "collection_id": cid,
                        "chunk_id": getattr(h, "chunk_id", ""),
                    })
            return results
        except Exception as exc:
            logger.warning("retriever_tool_kb_failed", error=str(exc)[:80])
            return []

    async def _search_kg(self, query: str, tenant_ctx: TenantContext) -> list[dict]:
        if not self._kg_store:
            return []
        try:
            nodes = self._kg_store.query_nodes(
                tenant_ctx.tenant_id, search=query.split()[0] if query else "", limit=5
            )
            results = []
            for node in nodes:
                edges = self._kg_store.get_edges_for_node(node.node_id, tenant_ctx.tenant_id)
                content = f"Entity: {node.label}. Type: {node.node_type.value}."
                if edges:
                    relations = ", ".join(
                        f"{e.edge_type.value} related" for e in edges[:3]
                    )
                    content += f" Relations: {relations}."
                results.append({
                    "content": content, "score": node.confidence * 0.8,
                    "source": "kg", "chunk_id": node.node_id,
                })
            return results
        except Exception as exc:
            logger.warning("retriever_tool_kg_failed", error=str(exc)[:80])
            return []

    async def _search_web(self, query: str, tenant_ctx: TenantContext) -> list[dict]:
        if not self._web_search:
            try:
                from app.tools.web_search import WebSearchTool
                self._web_search = WebSearchTool()
            except Exception:
                return []
        try:
            result = await self._web_search.search(query, num_results=5)
            chunks = []
            for r in result.results:
                chunks.append({
                    "content": f"{r.title}\n{r.snippet}",
                    "score": 0.6,  # web results get neutral confidence
                    "source": "web",
                    "url": r.url,
                })
            return chunks
        except Exception as exc:
            logger.warning("retriever_tool_web_failed", error=str(exc)[:80])
            return []

    async def _search_memory(self, query: str, tenant_ctx: TenantContext) -> list[dict]:
        results = []
        if self._exec_memory:
            try:
                plans = self._exec_memory.recall(goal_hint=query, tenant_ctx=tenant_ctx, top_k=3)
                for p in plans:
                    plan_text = str(p.get("plan", []))
                    results.append({
                        "content": f"[Past plan] {plan_text[:200]}",
                        "score": 0.5, "source": "memory",
                    })
            except Exception:
                pass
        if self._long_term_memory:
            try:
                memories = self._long_term_memory.recall(
                    query=query, tenant_ctx=tenant_ctx, top_k=3
                )
                for m in memories:
                    results.append({
                        "content": getattr(m, "content", str(m))[:200],
                        "score": 0.55, "source": "ltm",
                    })
            except Exception:
                pass
        return results


# Module-level singleton wired by create_app lifespan
retriever_tool = RetrieverTool()
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
cd agent-verse-backend && uv run pytest tests/rag/test_retriever_tool.py -v --no-cov 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add app/rag/retriever_tool.py tests/rag/test_retriever_tool.py
git commit -m "feat(rag): RetrieverTool unified interface with graceful degradation"
```

---

### Task A2: Wire `RetrieverTool` in `create_app` lifespan

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Read `app/main.py` around lifespan (lines 620–730)**

- [ ] **Step 2: Add wiring after `knowledge_store`, `kg_store`, etc. are set**

```python
# In lifespan, after all services are wired:
from app.rag.retriever_tool import retriever_tool
retriever_tool.set_dependencies(
    knowledge_store=app.state.knowledge_store,
    kg_store=getattr(app.state, "kg_store", None),
    exec_memory=getattr(app.state, "exec_memory", None),
    long_term_memory=getattr(app.state, "long_term_memory", None),
    embedder=getattr(app.state, "embedder", None),
    provider=getattr(app.state, "_app_provider", None),
    db=db_factory,
)
app.state.retriever_tool = retriever_tool
logger.info("retriever_tool_wired")
```

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat(rag): wire RetrieverTool dependencies in create_app lifespan"
```

---

### Task A3: Add source_inventory to planner prompt + auto web search

**Files:**
- Modify: `app/agent/graph.py` (`_node_rag_prime` replaces `_node_rag_retrieval`)
- Modify: `app/agent/prompts.py`

- [ ] **Step 1: Replace `_node_rag_retrieval` with `_node_rag_prime`**

The new node fires ALL sources in parallel using `asyncio.gather` and builds a `source_inventory` for the planner. If KB is empty, it auto-fires web search.

```python
async def _node_rag_prime(self, state: GraphState) -> dict:
    """Parallel comprehensive retrieval across all sources before planning."""
    agent_state = state["agent_state"]
    tenant_ctx = state["tenant_ctx"]
    
    # Build inventory first
    retriever = getattr(self, "_retriever_tool", None)
    if retriever is None:
        from app.rag.retriever_tool import retriever_tool
        retriever = retriever_tool
    
    inventory = await retriever.build_source_inventory(tenant_ctx)
    agent_state.context["source_inventory"] = inventory
    
    # Parallel retrieval across all sources
    tasks = []
    
    # Primary: KB + KG + Memory in parallel
    tasks.append(retriever.retrieve(
        agent_state.goal, tenant_ctx=tenant_ctx,
        strategy="hybrid", top_k=5,
        allow_web_fallback=(inventory["kb_collections"] == 0),
    ))
    
    # Always retrieve execution memory (different from KB)
    if self._exec_memory:
        tasks.append(retriever.retrieve(
            agent_state.goal, tenant_ctx=tenant_ctx,
            strategy="memory", top_k=3,
        ))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    context_parts = []
    all_citations = []
    
    for result in results:
        if isinstance(result, Exception):
            continue
        if result.context and result.source != "parametric":
            context_parts.append(
                f"[Source: {result.source}, confidence: {result.confidence:.2f}, "
                f"strategy: {result.strategy_used}]\n{result.context}"
            )
            all_citations.extend(result.citations)
        elif result.source == "parametric" and not context_parts:
            # Only add parametric notice if ALL other sources also empty
            context_parts.append(result.context)
    
    rag_context = "\n\n---\n\n".join(context_parts)
    agent_state.context["rag_citations"] = [
        {"index": c.index, "content": c.content[:200], "source": c.source, "score": c.score}
        for c in all_citations[:10]
    ]
    
    # Emit structured retrieval event
    await self._emit({
        "type": "rag_prime_complete",
        "sources_hit": [r.source for r in results if not isinstance(r, Exception)],
        "total_citations": len(all_citations),
        "kb_collections": inventory["kb_collections"],
        "web_used": any(
            not isinstance(r, Exception) and r.fallback_used for r in results
        ),
        "retrieval_mode": "parametric_only" if not all_citations else "external",
    })
    
    return {"rag_context": rag_context, "agent_state": agent_state}
```

- [ ] **Step 2: Update `PLANNER_SYSTEM` in `prompts.py`**

Add a section that receives source_inventory:

```python
PLANNER_SYSTEM = """You are a world-class autonomous agent planner...

[RETRIEVAL DIRECTIVES]
You can request specific retrieval in your steps using directives:
  [SEARCH:kb:"query"]     — search knowledge base
  [SEARCH:web:"query"]    — search the web
  [SEARCH:graph:"entity"] — explore knowledge graph
  [SEARCH:memory:"query"] — recall past plans

Example step with directive:
  "Step 2: [SEARCH:kb:"authentication flow"] Review the auth implementation"

Use directives when you need current information, specific domain knowledge,
or when the context provided is insufficient for a step.
"""
```

- [ ] **Step 3: Update graph routing to use new node name**

```python
g.add_node("rag_prime", self._node_rag_prime)
g.add_edge("initialize", "rag_prime")
g.add_edge("rag_prime", "plan")  # or "think" if CoT enabled
```

- [ ] **Step 4: Run existing tests**

```bash
cd agent-verse-backend && uv run pytest tests/agent/ -q --no-cov -m "not integration and not slow" 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add app/agent/graph.py app/agent/prompts.py
git commit -m "feat(rag): replace _node_rag_retrieval with _node_rag_prime (parallel, web fallback)"
```

---

## Phase B: Agentic Control

### Task B1: `rag_remediate` node + context-gap routing

**Files:**
- Modify: `app/agent/graph.py`

- [ ] **Step 1: Add `_node_rag_remediate`**

```python
async def _node_rag_remediate(self, state: GraphState) -> dict:
    """Re-retrieve when verification fails due to context gap."""
    agent_state = state["agent_state"]
    tenant_ctx = state["tenant_ctx"]
    
    feedback = agent_state.verification_feedback or ""
    count = agent_state.context.get("remediation_count", 0)
    
    # Extract missing topics from feedback
    missing_topics = self._extract_missing_topics(feedback, agent_state.goal)
    
    from app.rag.retriever_tool import retriever_tool
    
    remediation_contexts = []
    for topic in missing_topics[:2]:  # max 2 topics per remediation
        result = await retriever_tool.retrieve(
            topic,
            tenant_ctx=tenant_ctx,
            strategy="auto",
            allow_web_fallback=True,
            min_confidence=0.0,  # accept anything
        )
        if result.context:
            remediation_contexts.append(
                f"[Remediation {count+1} — topic: {topic}]\n{result.context}"
            )
    
    if remediation_contexts:
        existing = agent_state.context.get("rag_context", "")
        agent_state.context["rag_context"] = (
            existing + "\n\n" + "\n\n".join(remediation_contexts)
        )
    
    agent_state.context["remediation_count"] = count + 1
    
    await self._emit({
        "type": "rag_remediation",
        "iteration": count + 1,
        "topics_searched": missing_topics,
        "context_added": bool(remediation_contexts),
    })
    
    return {"agent_state": agent_state}

def _extract_missing_topics(self, feedback: str, goal: str) -> list[str]:
    """Heuristically extract what's missing from verification feedback."""
    topics = []
    # Simple extraction: look for quoted terms or "about X" patterns
    import re
    quoted = re.findall(r'"([^"]{3,50})"', feedback)
    topics.extend(quoted[:2])
    about = re.findall(r"about ([a-zA-Z0-9 ]{3,40}?)(?:[,.]|$)", feedback.lower())
    topics.extend(about[:2])
    if not topics:
        # Fall back: use the goal itself as a broader search
        topics = [goal[:100]]
    return list(dict.fromkeys(topics))  # deduplicate
```

- [ ] **Step 2: Upgrade `_route` for context-gap detection**

```python
# In _route(), before the "replan" return:
feedback_lower = (agent_state.verification_feedback or "").lower()
_is_context_gap = any(s in feedback_lower for s in _CONTEXT_GAP_SIGNALS)
_remediation_count = agent_state.context.get("remediation_count", 0)

if _is_context_gap and _remediation_count < 2:
    return "rag_remediate"
```

- [ ] **Step 3: Register node and edge in `build_graph`**

```python
g.add_node("rag_remediate", self._node_rag_remediate)
routing_map["rag_remediate"] = "rag_remediate"  # add to conditional edges
g.add_edge("rag_remediate", "plan")  # after remediation, replan
```

- [ ] **Step 4: Test**

```bash
cd agent-verse-backend && uv run pytest tests/agent/ tests/rag/ -q --no-cov -m "not integration and not slow" 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add app/agent/graph.py
git commit -m "feat(rag): add rag_remediate node — re-retrieval on context-gap verification failure"
```

---

### Task B2: Parse `[SEARCH:...]` directives in execute node

**Files:**
- Modify: `app/agent/graph.py` (`_execute_step`)

- [ ] **Step 1: Add directive parser**

```python
def _parse_search_directives(self, step: str) -> list[dict]:
    """Extract [SEARCH:source:"query"] from a step description."""
    import re
    pattern = r'\[SEARCH:(\w+):"([^"]+)"\]'
    return [
        {"source": m.group(1), "query": m.group(2)}
        for m in re.finditer(pattern, step)
    ]
```

- [ ] **Step 2: In `_execute_step`, fire directives before LLM call**

```python
# After query embedding, before executor LLM call:
directives = self._parse_search_directives(step)
directive_context = []
if directives:
    from app.rag.retriever_tool import retriever_tool
    for d in directives:
        result = await retriever_tool.retrieve(
            d["query"], tenant_ctx=tenant_ctx,
            strategy=d["source"],
            allow_web_fallback=(d["source"] == "web"),
        )
        if result.context:
            directive_context.append(
                f"[Retrieved for step — source: {result.source}]\n{result.context}"
            )
if directive_context:
    step_context_extra = "\n\n".join(directive_context)
    # Prepend to existing step_context
    step_context = step_context_extra + "\n\n" + (step_context or "")
```

- [ ] **Step 3: Test + commit**

```bash
cd agent-verse-backend && uv run pytest tests/agent/ -q --no-cov -m "not integration and not slow" 2>&1 | tail -5
git add app/agent/graph.py
git commit -m "feat(rag): parse [SEARCH:source:query] directives in execute step"
```

---

## Phase C: Quality

### Task C1: Thread citations through StepResult + verifier

**Files:**
- Modify: `app/agent/state.py`
- Modify: `app/agent/graph.py` (`_node_verify`)
- Modify: `app/agent/prompts.py`

- [ ] **Step 1: Add citation fields to `StepResult`**

```python
@dataclass
class StepResult:
    description: str
    status: StepStatus = StepStatus.PENDING
    output: str = ""
    error: str = ""
    step_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    started_at: str | None = None
    completed_at: str | None = None
    # RAG provenance
    citations: list[dict] = field(default_factory=list)
    retrieval_confidence: float = 0.0
    sources_used: list[str] = field(default_factory=list)
    retrieval_strategy: str = ""
```

- [ ] **Step 2: Pass citations to verifier system prompt**

```python
# In _node_verify, build citations summary:
citations_summary = ""
for step in agent_state.steps:
    if step.citations:
        cit_text = "; ".join(f"[{c['index']}] {c['content'][:80]}" for c in step.citations[:3])
        citations_summary += f"Step '{step.description[:50]}' sources: {cit_text}\n"

# Inject into verifier user content:
user_content = (
    f"Goal: {agent_state.goal}\n"
    f"Executed steps:\n{summary}\n"
    + (f"\nSource citations used:\n{citations_summary}" if citations_summary else "")
    + "\nVerify: did the agent use appropriate sources? Are claims grounded?"
)
```

- [ ] **Step 3: Test + commit**

```bash
git add app/agent/state.py app/agent/graph.py app/agent/prompts.py
git commit -m "feat(rag): thread citations through StepResult and verifier"
```

---

### Task C2: Add retrieval events + metrics to AgentRunTrace

**Files:**
- Modify: `app/agent_runtime/models.py`
- Modify: `app/agent/graph.py`

- [ ] **Add to `AgentRunTrace`:**

```python
@dataclass
class AgentRunTrace:
    ...
    # RAG telemetry
    retrieval_calls: int = 0
    retrieval_hits: int = 0        # calls that returned non-empty results
    web_search_calls: int = 0
    parametric_fallbacks: int = 0  # steps with no external context
    avg_retrieval_confidence: float = 0.0
    sources_used: list[str] = field(default_factory=list)
```

- [ ] **Track in RetrieverTool.retrieve() calls via current_trace**

- [ ] **Test + commit**

---

## Verification Commands (run after each phase)

```bash
# Phase A
cd agent-verse-backend && uv run pytest tests/rag/ tests/agent/test_agent_loop.py -q --no-cov 2>&1 | tail -10

# Phase B  
cd agent-verse-backend && uv run pytest tests/rag/ tests/agent/ -q --no-cov -m "not integration and not slow" 2>&1 | tail -10

# Phase C
cd agent-verse-backend && uv run pytest tests/ -q --no-cov -m "not integration and not slow" --ignore=tests/live 2>&1 | tail -10

# Live end-to-end test
API_KEY="..." 
curl -X POST http://localhost:8000/goals \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"goal":"Explain the CAP theorem","priority":"normal","dry_run":false}'
# Stream events — should see rag_prime_complete with sources
```

## Summary

| Phase | Tasks | New Files | Key Outcome |
|---|---|---|---|
| A | A1, A2, A3 | `retriever_tool.py` | Unified retrieval, web fallback, parallel sources |
| B | B1, B2 | — | rag_remediate node, [SEARCH:] directives |
| C | C1, C2 | — | Citations threaded, verifier grounded, RAG trace |
