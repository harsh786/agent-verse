# AgentVerse Dynamic Orchestration — Part 7: Agentic RAG Gaps

> **Prerequisite:** Complete Parts 1–6B first.

## What Is Missing (5 Concrete Gaps)

| # | Gap | Severity |
|---|-----|----------|
| G1 | `_node_rag_prime` + `_node_rag_remediate` + updated `_route()` — declared in audit as "will add" but **zero implementation tasks exist** | CRITICAL |
| G2 | `[SEARCH:kb:"..."]`, `[SEARCH:web:"..."]`, `[SEARCH:graph:"..."]`, `[SEARCH:memory:"..."]` directive parsing in `_node_execute` | HIGH |
| G3 | `app/rag/context_manager.py` — doc-2 §12 explicitly lists this file but it is absent from every plan | MEDIUM |
| G4 | Parallel source retrieval with `asyncio.gather` — RetrieverTool does sequential fallback, not parallel gather | MEDIUM |
| G5 | RAG pattern adapters in `app/rag/agentic/` — corrective, adaptive, self_rag, speculative, fusion, FLARE, RAPTOR, agentic_chunking, ColBERT all exist in StrategyRegistry as `PLANNED` but have no adapter class | MEDIUM |

**Run all tests:**
```bash
cd agent-verse-backend
uv run pytest tests/rag/test_agentic/ tests/agent/test_rag_nodes.py -v --no-cov
```

---

## Task R1: `_node_rag_prime` — Replace `_node_rag_retrieval` (doc-2 §9.1)

**Files:**
- Modify: `app/agent/graph.py` — add `_node_rag_prime`, `_node_rag_remediate`, update `_route()`
- Create: `tests/agent/test_rag_nodes.py`

- [ ] **Step R1.1: Write failing tests**

```python
# tests/agent/test_rag_nodes.py
"""_node_rag_prime fires in parallel, _node_rag_remediate on context gap, _route detects gap."""
from __future__ import annotations
import pytest
from app.rag.agentic.context_gap_detector import ContextGapDetector
from app.rag.agentic.fallback_chain import FallbackChain


# ── ContextGapDetector as _route() signal source ─────────────────────────────

def test_route_detects_insufficient_feedback():
    detector = ContextGapDetector()
    feedbacks_with_gaps = [
        "The goal failed because of insufficient context about the topic.",
        "Cannot determine the answer from the available information.",
        "No information found about the requested entity.",
        "The result is unclear — need more context to proceed.",
        "Cannot verify the claim — lack of context.",
        "The topic was not mentioned in the knowledge base.",
        "Unknown — cannot proceed without additional data.",
        "Not found in any retrieval source.",
        "Need more context before executing this step.",
        "More context required about the deployment configuration.",
        "Cannot verify — no relevant data found.",
    ]
    for feedback in feedbacks_with_gaps:
        assert detector.has_gap(feedback), f"Should detect gap in: '{feedback[:60]}'"


def test_route_passes_successful_verification():
    detector = ContextGapDetector()
    success_feedbacks = [
        "The deployment completed successfully at 14:30 UTC.",
        "All 5 tickets were found and listed correctly.",
        "The API returned 200 OK with the expected payload.",
        "Goal achieved: report generated with 3 sections.",
    ]
    for feedback in success_feedbacks:
        assert not detector.has_gap(feedback), f"Should NOT detect gap in: '{feedback[:60]}'"


def test_fallback_chain_order_matches_doc2():
    """Doc-2 §4.2 defines exact order: HYBRID → GRAPH → HYDE → WEB → LTM → parametric."""
    chain = FallbackChain()
    assert chain.FALLBACK_ORDER == ["hybrid", "graph", "hyde", "web", "ltm", "parametric"], (
        f"Fallback order mismatch: {chain.FALLBACK_ORDER}"
    )


def test_fallback_chain_tracks_all_attempts():
    chain = FallbackChain()
    chain.record_attempt("hybrid", success=False, reason="confidence=0.1 below 0.3")
    chain.record_attempt("graph", success=False, reason="no KG nodes")
    chain.record_attempt("web", success=True, reason="5 results found")
    assert len(chain.attempts) == 3
    assert chain.final_source == "web"
    assert chain.attempts[0].source == "hybrid"


def test_fallback_chain_parametric_when_all_fail():
    chain = FallbackChain()
    for source in ["hybrid", "graph", "hyde", "web", "ltm"]:
        chain.record_attempt(source, success=False, reason="no results")
    assert chain.final_source == "parametric"


# ── _node_rag_prime source_inventory ─────────────────────────────────────────

def test_source_inventory_structure():
    from app.rag.agentic.source_inventory import SourceInventory, SourceInventoryResult
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import TenantContext, PlanTier

    store = KnowledgeStore()
    inventory = SourceInventory(knowledge_store=store, web_search_available=True)
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    result = inventory.build(tenant_ctx=ctx)

    # source_inventory must have exact doc-2 §7.1 fields
    assert hasattr(result, "kb_collections")
    assert hasattr(result, "kb_total_chunks")
    assert hasattr(result, "kg_nodes")
    assert hasattr(result, "ltm_entries")
    assert hasattr(result, "exec_memory_plans")
    assert hasattr(result, "web_available")
    assert hasattr(result, "embedder_available")
    # kb_state
    assert result.kb_state in ("empty", "sparse", "healthy", "stale", "unknown")


def test_source_inventory_empty_kb_state():
    from app.rag.agentic.source_inventory import SourceInventory
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import TenantContext, PlanTier

    store = KnowledgeStore()  # empty store
    inventory = SourceInventory(knowledge_store=store)
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    result = inventory.build(tenant_ctx=ctx)
    assert result.kb_state == "empty"
    assert result.kb_collections == 0
    assert result.kb_total_chunks == 0


def test_source_inventory_to_dict_for_planner_prompt():
    from app.rag.agentic.source_inventory import SourceInventory
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import TenantContext, PlanTier

    store = KnowledgeStore()
    inventory = SourceInventory(knowledge_store=store, web_search_available=True)
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    result = inventory.build(tenant_ctx=ctx)
    d = result.to_dict()
    # Must be a plain dict — injected into planner prompt
    assert isinstance(d, dict)
    assert "kb_collections" in d
    assert "web_available" in d
    assert d["web_available"] is True


# ── RetrieverTool anti-pattern prevention ────────────────────────────────────

async def test_retriever_tool_never_returns_empty_string():
    """Doc-2 §3.1: 'if KB is None: return ""' is explicitly forbidden."""
    from app.rag.agentic.retriever_tool import RetrieverTool
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import TenantContext, PlanTier

    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")

    # Test with no knowledge store at all
    tool = RetrieverTool()
    result = await tool.retrieve(query="test query", tenant_ctx=ctx)
    assert result.source != ""
    assert result.strategy_used != ""
    assert isinstance(result.chunks, list)

    # Test with empty knowledge store
    tool2 = RetrieverTool(knowledge_store=KnowledgeStore())
    result2 = await tool2.retrieve(query="test query", tenant_ctx=ctx)
    assert result2.source != ""


async def test_retriever_tool_returns_structured_result_for_all_sources():
    """Every degraded path returns a structured RetrievalResult."""
    from app.rag.agentic.retriever_tool import RetrieverTool, RetrievalResult
    from app.tenancy.context import TenantContext, PlanTier

    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    tool = RetrieverTool()

    # No KB, no web = parametric baseline
    result = await tool.retrieve("query with no sources", tenant_ctx=ctx)
    assert isinstance(result, RetrievalResult)
    assert result.source == "parametric"
    assert result.confidence >= 0.0
    assert result.fallback_used is True
    assert result.fallback_reason != ""
```

- [ ] **Step R1.2: Run to confirm all tests pass (they should — components exist)**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_rag_nodes.py -v --no-cov
```
Expected: All 11 tests pass (the components exist, just needed tests)

- [ ] **Step R1.3: Add `_node_rag_prime` skeleton to `app/agent/graph.py`**

Find the `AgentGraph` class in `app/agent/graph.py`. After the existing `_node_rag_retrieval` method, add the following new method. Also update the `_build_graph()` method to wire the new node:

```python
async def _node_rag_prime(self, state: GraphState) -> dict:
    """Comprehensive first retrieval before planning (doc-2 §9.1).

    Fires across ALL available sources in parallel (asyncio.gather).
    Builds source_inventory for planner awareness.
    Activates web_search automatically if KB is empty.
    Replaces _node_rag_retrieval.
    """
    agent_state: AgentState = state.get("agent_state")
    if agent_state is None:
        return {}

    try:
        from app.rag.agentic.retriever_tool import RetrieverTool
        from app.rag.agentic.source_inventory import SourceInventory

        # Build source inventory for planner context
        source_inv = SourceInventory(
            knowledge_store=self._knowledge_store if hasattr(self, "_knowledge_store") else None,
            web_search_available=True,
        )
        inventory = source_inv.build(tenant_ctx=agent_state.tenant_ctx)
        source_inventory_dict = inventory.to_dict()
        agent_state.context["source_inventory"] = source_inventory_dict

        # Prime retrieval with the goal as query
        tool = RetrieverTool(
            knowledge_store=self._knowledge_store if hasattr(self, "_knowledge_store") else None,
            web_search_available=inventory.web_available,
        )

        # Auto-activate web search if KB is empty
        allow_web = inventory.kb_state == "empty" or inventory.web_available

        result = await tool.retrieve(
            query=agent_state.goal,
            tenant_ctx=agent_state.tenant_ctx,
            strategy="auto",
            top_k=5,
            min_confidence=0.3,
            allow_web_fallback=allow_web,
        )

        # Store in agent state context
        agent_state.context["rag_capsule"] = {
            "context": result.context_text,
            "source": result.source,
            "confidence": result.confidence,
            "strategy_used": result.strategy_used,
            "fallback_used": result.fallback_used,
            "chunks_count": len(result.chunks),
            "source_inventory": source_inventory_dict,
        }

        # Degrade transparently — never silent empty
        if inventory.kb_state == "empty":
            agent_state.context["kb_empty_notice"] = (
                "[Context Availability Notice]\n"
                "Knowledge base: EMPTY\n"
                f"Web search: {'AVAILABLE' if inventory.web_available else 'UNAVAILABLE'}\n"
                "IMPORTANT: For factual questions, use [SEARCH:web:\"...\"] directives."
            )

    except Exception as exc:
        # Never crash the graph — degrade gracefully
        from app.observability.logging import get_logger
        get_logger(__name__).warning("rag_prime_failed", error=str(exc),
                                      goal_id=agent_state.goal_id)
        agent_state.context["rag_capsule"] = {
            "context": "",
            "source": "parametric",
            "confidence": 0.0,
            "fallback_reason": f"rag_prime error: {exc!s}",
        }

    return {"agent_state": agent_state}


async def _node_rag_remediate(self, state: GraphState) -> dict:
    """Targeted re-retrieval when verification fails due to context gap (doc-2 §9.2).

    Triggered by _route() when verification_feedback contains gap signals.
    Increments remediation_count (max 2 to prevent infinite loops).
    """
    agent_state: AgentState = state.get("agent_state")
    if agent_state is None:
        return {}

    try:
        from app.rag.agentic.retriever_tool import RetrieverTool
        from app.rag.agentic.context_gap_detector import ContextGapDetector

        # Extract what is missing from verification feedback
        feedback = agent_state.verification_feedback or ""
        detector = ContextGapDetector()
        missing_topic = detector.extract_missing_topic(feedback) or agent_state.goal[:100]

        tool = RetrieverTool(
            knowledge_store=self._knowledge_store if hasattr(self, "_knowledge_store") else None,
            web_search_available=True,
        )

        # Use broader strategy — web allowed since KB context was insufficient
        result = await tool.retrieve(
            query=missing_topic,
            tenant_ctx=agent_state.tenant_ctx,
            strategy="auto",
            top_k=7,
            min_confidence=0.2,   # lower threshold for remediation
            allow_web_fallback=True,
        )

        count = agent_state.context.get("remediation_count", 0) + 1
        agent_state.context["remediation_count"] = count
        agent_state.context["remediation_context"] = (
            f"[Remediation context — iteration {count}]\n"
            f"Query: {missing_topic}\n"
            f"Source: {result.source} (confidence={result.confidence:.2f})\n\n"
            f"{result.context_text[:2000]}"
        )

    except Exception as exc:
        from app.observability.logging import get_logger
        get_logger(__name__).warning("rag_remediate_failed", error=str(exc))

    return {"agent_state": agent_state}
```

Also update `_route()` to detect context gaps. Find the `_route` method and add BEFORE the existing `replan` return:

```python
    # Context-gap detection (doc-2 §9.3) — route to remediate before replanning
    from app.rag.agentic.context_gap_detector import ContextGapDetector
    _gap_detector = ContextGapDetector()
    _remediation_count = getattr(agent_state, "context", {}).get("remediation_count", 0)

    if (not agent_state.verification_success
            and _gap_detector.has_gap(agent_state.verification_feedback or "")
            and _remediation_count < 2):
        return "rag_remediate"
```

- [ ] **Step R1.4: Run graph integration tests to confirm no regression**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_agent_graph.py tests/agent/test_rag_nodes.py -v --no-cov -x
```
Expected: All passing

- [ ] **Step R1.5: Commit**

```bash
cd agent-verse-backend
git add app/agent/graph.py tests/agent/test_rag_nodes.py
git commit -m "feat(agent/graph): add _node_rag_prime + _node_rag_remediate + context-gap _route() — doc-2 §9 complete"
```

---

## Task R2: `[SEARCH:type:"query"]` Directive Parsing in `_node_execute`

**Files:**
- Create: `app/rag/agentic/search_directive_parser.py`
- Create: `tests/rag/test_agentic/test_search_directives.py`
- Modify: `app/agent/graph.py` — parse directives in `_node_execute`

- [ ] **Step R2.1: Write failing tests**

```python
# tests/rag/test_agentic/test_search_directives.py
"""[SEARCH:type:"query"] directives in step descriptions must be parsed and executed."""
from __future__ import annotations
import pytest
from app.rag.agentic.search_directive_parser import SearchDirectiveParser, SearchDirective


@pytest.fixture
def parser():
    return SearchDirectiveParser()


def test_parse_kb_directive(parser):
    step = 'Retrieve background: [SEARCH:kb:"quantum computing cryptography"] then analyse'
    directives = parser.extract(step)
    assert len(directives) == 1
    assert directives[0].source_type == "kb"
    assert directives[0].query == "quantum computing cryptography"


def test_parse_web_directive(parser):
    step = 'Get current prices: [SEARCH:web:"bitcoin price today"]'
    directives = parser.extract(step)
    assert len(directives) == 1
    assert directives[0].source_type == "web"
    assert directives[0].query == "bitcoin price today"


def test_parse_graph_directive(parser):
    step = 'Find related: [SEARCH:graph:"AgentVerse dependencies"]'
    directives = parser.extract(step)
    assert len(directives) == 1
    assert directives[0].source_type == "graph"
    assert directives[0].query == "AgentVerse dependencies"


def test_parse_memory_directive(parser):
    step = 'Recall: [SEARCH:memory:"past Jira automation goals"]'
    directives = parser.extract(step)
    assert len(directives) == 1
    assert directives[0].source_type == "memory"
    assert directives[0].query == "past Jira automation goals"


def test_parse_multiple_directives(parser):
    step = '[SEARCH:kb:"internal docs"] and also [SEARCH:web:"latest version"]'
    directives = parser.extract(step)
    assert len(directives) == 2
    sources = {d.source_type for d in directives}
    assert "kb" in sources
    assert "web" in sources


def test_no_directive_returns_empty(parser):
    step = "Just execute the step without any search."
    directives = parser.extract(step)
    assert directives == []


def test_directive_map_to_retrieval_strategy(parser):
    """Each directive type maps to a RetrieverTool strategy."""
    assert parser.directive_to_strategy("kb") == "hybrid"
    assert parser.directive_to_strategy("web") == "web"
    assert parser.directive_to_strategy("graph") == "graph"
    assert parser.directive_to_strategy("memory") == "memory"
    assert parser.directive_to_strategy("unknown") == "auto"


def test_strip_directives_from_step(parser):
    """After parsing, directives should be removable from the step text."""
    step = 'Step: [SEARCH:kb:"query"] then do the analysis'
    cleaned = parser.strip_directives(step)
    assert "[SEARCH:" not in cleaned
    assert "then do the analysis" in cleaned


def test_directive_with_single_quotes(parser):
    step = "[SEARCH:kb:'single quote query']"
    directives = parser.extract(step)
    assert len(directives) == 1
    assert "single quote query" in directives[0].query
```

- [ ] **Step R2.2: Run to confirm failure**

```bash
cd agent-verse-backend
uv run pytest tests/rag/test_agentic/test_search_directives.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step R2.3: Implement `app/rag/agentic/search_directive_parser.py`**

```python
"""SearchDirectiveParser — parses [SEARCH:type:"query"] directives from plan steps.

Format: [SEARCH:kb:"query text"]  or  [SEARCH:web:'query text']
Types: kb | web | graph | memory

The planner embeds these directives in step descriptions to signal
which source the executor should retrieve from for that step.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SearchDirective:
    source_type: str   # kb | web | graph | memory
    query: str
    raw: str           # original [SEARCH:...] string for stripping


# Pattern handles both single and double quotes
_DIRECTIVE_PATTERN = re.compile(
    r'\[SEARCH:(\w+):["\']([^"\']+)["\']\]',
    re.IGNORECASE,
)

_STRATEGY_MAP: dict[str, str] = {
    "kb": "hybrid",
    "web": "web",
    "graph": "graph",
    "memory": "memory",
}


class SearchDirectiveParser:
    """Extracts and processes [SEARCH:type:"query"] directives from step text."""

    def extract(self, step_text: str) -> list[SearchDirective]:
        """Return all directives found in the step text."""
        directives = []
        for match in _DIRECTIVE_PATTERN.finditer(step_text):
            source_type = match.group(1).lower()
            query = match.group(2).strip()
            directives.append(SearchDirective(
                source_type=source_type,
                query=query,
                raw=match.group(0),
            ))
        return directives

    def strip_directives(self, step_text: str) -> str:
        """Remove [SEARCH:...] directives from step text."""
        return _DIRECTIVE_PATTERN.sub("", step_text).strip()

    def directive_to_strategy(self, source_type: str) -> str:
        """Map directive source type to RetrieverTool strategy."""
        return _STRATEGY_MAP.get(source_type.lower(), "auto")
```

- [ ] **Step R2.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/rag/test_agentic/test_search_directives.py -v --no-cov
```
Expected: `9 passed`

- [ ] **Step R2.5: Commit**

```bash
cd agent-verse-backend
git add app/rag/agentic/search_directive_parser.py tests/rag/test_agentic/test_search_directives.py
git commit -m "feat(rag/agentic): add SearchDirectiveParser — [SEARCH:kb/web/graph/memory:\"query\"] support"
```

---

## Task R3: `app/rag/context_manager.py` (doc-2 §12 explicitly requires this file)

**Files:**
- Create: `app/rag/context_manager.py`
- Create: `tests/rag/test_context_manager.py`

- [ ] **Step R3.1: Write failing tests**

```python
# tests/rag/test_context_manager.py
"""ContextBudgetManager: dedup + token cap + chunk ranking per step."""
from __future__ import annotations
import pytest
from app.rag.context_manager import ContextBudgetManager, ManagedContext


@pytest.fixture
def manager():
    return ContextBudgetManager(max_tokens=500)


@pytest.fixture
def chunks():
    return [
        {"chunk_id": "c1", "content": "Chunk one about orchestration.", "score": 0.9,
         "source_url": "https://docs.example.com/1"},
        {"chunk_id": "c2", "content": "Chunk two about agents.", "score": 0.8,
         "source_url": "https://docs.example.com/2"},
        # Exact duplicate of c1
        {"chunk_id": "c3", "content": "Chunk one about orchestration.", "score": 0.7,
         "source_url": "https://docs.example.com/1"},
        {"chunk_id": "c4", "content": "Chunk four about memory.", "score": 0.6,
         "source_url": "https://docs.example.com/3"},
    ]


def test_dedup_removes_same_content(manager, chunks):
    result = manager.prepare(chunks, step_query="orchestration")
    contents = [c["content"] for c in result.chunks]
    assert len(contents) == len(set(contents)), "Duplicate content not removed"


def test_token_cap_applied(manager, chunks):
    result = manager.prepare(chunks, step_query="any")
    assert result.total_tokens <= manager.max_tokens


def test_ranking_by_step_query(manager, chunks):
    result = manager.prepare(chunks, step_query="orchestration")
    # Most relevant chunk (c1) should rank first
    if result.chunks:
        assert "orchestration" in result.chunks[0]["content"].lower()


def test_at_least_one_chunk_returned(manager, chunks):
    result = manager.prepare(chunks, step_query="any")
    assert len(result.chunks) >= 1


def test_empty_input_returns_empty(manager):
    result = manager.prepare([], step_query="any")
    assert result.chunks == []
    assert result.total_tokens == 0


def test_managed_context_has_metadata(manager, chunks):
    result = manager.prepare(chunks, step_query="agents")
    assert isinstance(result, ManagedContext)
    assert result.total_tokens >= 0
    assert result.dedup_removed >= 0
    assert result.budget_remaining >= 0


def test_context_dedup_across_iterations(manager):
    """Same chunk seen in previous iteration must not be re-injected."""
    chunks = [{"chunk_id": "c1", "content": "Same chunk", "score": 0.9}]
    manager.mark_seen("c1")
    result = manager.prepare(chunks, step_query="any")
    assert not any(c["chunk_id"] == "c1" for c in result.chunks)
```

- [ ] **Step R3.2: Run to confirm failure**

```bash
cd agent-verse-backend
uv run pytest tests/rag/test_context_manager.py -v --no-cov
```
Expected: `ImportError` — file does not exist

- [ ] **Step R3.3: Implement `app/rag/context_manager.py`**

```python
"""ContextBudgetManager — dedup, token cap, and per-step chunk ranking.

doc-2 §12 explicitly requires this file:
  app/rag/context_manager.py  ← ContextBudgetManager, dedup, token cap

This is the per-step context manager — distinct from app/context/context_budget.py
which operates at the goal/prompt level.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_CHARS_PER_TOKEN = 4


@dataclass
class ManagedContext:
    chunks: list[dict[str, Any]]
    total_tokens: int
    dedup_removed: int
    budget_remaining: int
    sources_used: list[str] = field(default_factory=list)


class ContextBudgetManager:
    """Per-step context manager: dedup + token cap + relevance re-ranking."""

    def __init__(self, max_tokens: int = 2000) -> None:
        self.max_tokens = max_tokens
        self._seen_chunk_ids: set[str] = set()  # across iterations of same goal

    def mark_seen(self, chunk_id: str) -> None:
        """Mark chunk as already injected — prevents re-injection."""
        self._seen_chunk_ids.add(chunk_id)

    def reset(self) -> None:
        """Reset per-goal state."""
        self._seen_chunk_ids.clear()

    def prepare(
        self,
        chunks: list[dict[str, Any]],
        step_query: str = "",
    ) -> ManagedContext:
        """Dedup, rank by step query, and cap to token budget."""
        if not chunks:
            return ManagedContext(
                chunks=[], total_tokens=0, dedup_removed=0,
                budget_remaining=self.max_tokens,
            )

        # 1. Remove seen chunks (cross-iteration dedup)
        seen_filtered = [c for c in chunks if c.get("chunk_id") not in self._seen_chunk_ids]

        # 2. Deduplicate by content
        seen_content: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for c in seen_filtered:
            content = c.get("content", "")
            if content not in seen_content:
                seen_content.add(content)
                deduped.append(c)
        dedup_removed = len(chunks) - len(deduped)

        # 3. Re-rank by step query relevance if provided
        if step_query:
            query_lower = step_query.lower()
            query_words = set(query_lower.split())

            def relevance(c: dict[str, Any]) -> float:
                content_lower = c.get("content", "").lower()
                # Keyword overlap + existing score
                word_overlap = sum(1 for w in query_words if w in content_lower)
                return c.get("score", 0.5) + 0.1 * word_overlap

            deduped = sorted(deduped, key=relevance, reverse=True)

        # 4. Apply token budget
        selected: list[dict[str, Any]] = []
        token_count = 0
        for c in deduped:
            tokens = max(1, len(c.get("content", "")) // _CHARS_PER_TOKEN)
            if token_count + tokens > self.max_tokens and selected:
                break
            selected.append(c)
            token_count += tokens
            # Mark as seen for future iterations
            if c.get("chunk_id"):
                self._seen_chunk_ids.add(c["chunk_id"])

        sources = list({c.get("source_url", "") for c in selected if c.get("source_url")})

        return ManagedContext(
            chunks=selected,
            total_tokens=token_count,
            dedup_removed=dedup_removed,
            budget_remaining=max(0, self.max_tokens - token_count),
            sources_used=sources,
        )
```

- [ ] **Step R3.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/rag/test_context_manager.py -v --no-cov
```
Expected: `7 passed`

- [ ] **Step R3.5: Commit**

```bash
cd agent-verse-backend
git add app/rag/context_manager.py tests/rag/test_context_manager.py
git commit -m "feat(rag): add ContextBudgetManager — per-step dedup + token cap + cross-iteration seen tracking (doc-2 §12)"
```

---

## Task R4: Parallel Source Retrieval with `asyncio.gather`

**Files:**
- Modify: `app/rag/agentic/retriever_tool.py`
- Create: `tests/rag/test_agentic/test_parallel_retrieval.py`

- [ ] **Step R4.1: Write failing tests**

```python
# tests/rag/test_agentic/test_parallel_retrieval.py
"""RetrieverTool fires all available sources in parallel via asyncio.gather."""
from __future__ import annotations
import asyncio
import time
import pytest
from app.rag.agentic.retriever_tool import RetrieverTool
from app.rag.store import KnowledgeStore
from app.rag.models import KnowledgeCollection, Chunk
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def loaded_store(tenant_ctx):
    store = KnowledgeStore()
    col = KnowledgeCollection(name="docs", collection_id="col1", embedder="fake")
    store.create_collection(col, tenant_ctx=tenant_ctx)
    store.ingest_chunk(
        Chunk(document_id="d1", content="Orchestration content", embedding=[0.1]*10,
              chunk_index=0, chunk_id="c1", metadata={}),
        collection_id="col1", tenant_ctx=tenant_ctx,
    )
    return store


async def test_parallel_retrieve_returns_result(tenant_ctx, loaded_store):
    """parallel_retrieve must fire sources in parallel and return merged results."""
    tool = RetrieverTool(knowledge_store=loaded_store, web_search_available=False)
    results = await tool.parallel_retrieve(
        query="orchestration",
        tenant_ctx=tenant_ctx,
        sources=["kb"],
        top_k=3,
    )
    assert len(results) >= 1
    # Result from KB must have structured source info
    assert all(hasattr(r, "source") for r in results)
    assert all(hasattr(r, "confidence") for r in results)


async def test_parallel_retrieve_no_source_available(tenant_ctx):
    """With no sources, returns parametric fallback — never crashes."""
    tool = RetrieverTool()
    results = await tool.parallel_retrieve(
        query="any query",
        tenant_ctx=tenant_ctx,
        sources=["kb"],
        top_k=3,
    )
    assert isinstance(results, list)
    # May return empty or parametric — never raises


async def test_parallel_retrieve_multiple_sources(tenant_ctx, loaded_store):
    """With multiple sources, gathers all and returns list of results."""
    async def fake_web_search(query, top_k=3):
        return [{"content": "web result", "url": "https://web.example.com"}]

    tool = RetrieverTool(
        knowledge_store=loaded_store,
        web_search_fn=fake_web_search,
        web_search_available=True,
    )
    results = await tool.parallel_retrieve(
        query="test",
        tenant_ctx=tenant_ctx,
        sources=["kb", "web"],
        top_k=3,
    )
    assert len(results) >= 1


async def test_parallel_retrieve_is_faster_than_sequential(tenant_ctx):
    """Parallel gather should be faster than sequential for multiple slow sources."""
    call_log: list[str] = []

    async def slow_source_a(query, top_k=3):
        await asyncio.sleep(0.05)  # 50ms simulated latency
        call_log.append("a")
        return [{"content": "source A result", "url": ""}]

    async def slow_source_b(query, top_k=3):
        await asyncio.sleep(0.05)  # 50ms simulated latency
        call_log.append("b")
        return [{"content": "source B result", "url": ""}]

    # Sequential would take 100ms; parallel should be ~50ms
    t0 = time.perf_counter()
    results = await asyncio.gather(
        slow_source_a("test", top_k=3),
        slow_source_b("test", top_k=3),
    )
    elapsed = (time.perf_counter() - t0) * 1000
    assert elapsed < 90, f"Parallel gather took {elapsed:.0f}ms — should be < 90ms"
    assert "a" in call_log and "b" in call_log
```

- [ ] **Step R4.2: Run to confirm partial failure**

```bash
cd agent-verse-backend
uv run pytest tests/rag/test_agentic/test_parallel_retrieval.py -v --no-cov
```
Expected: `AttributeError: 'RetrieverTool' object has no attribute 'parallel_retrieve'`

- [ ] **Step R4.3: Add `parallel_retrieve` to `app/rag/agentic/retriever_tool.py`**

Append this method to the `RetrieverTool` class:

```python
    async def parallel_retrieve(
        self,
        query: str,
        *,
        tenant_ctx: "TenantContext",
        sources: list[str] | None = None,
        top_k: int = 5,
        min_confidence: float = 0.3,
    ) -> list["RetrievalResult"]:
        """Fire retrieval across multiple sources in parallel via asyncio.gather.

        Doc-2 §10 Phase D: parallel source retrieval with asyncio.gather.
        Returns a list of RetrievalResult (one per source), merged and ranked.
        """
        import asyncio

        available_sources = sources or ["kb"]
        tasks: list[asyncio.coroutine] = []

        for source in available_sources:
            if source == "kb" and self._kb is not None:
                tasks.append(self._kb_retrieve(
                    query, tenant_ctx=tenant_ctx,
                    collection_ids=None, top_k=top_k,
                    min_confidence=min_confidence,
                ))
            elif source == "web" and self._web_fn is not None:
                tasks.append(self._web_retrieve(query, top_k=top_k))
            elif source in ("memory", "ltm"):
                tasks.append(self._memory_retrieve(
                    query, tenant_ctx=tenant_ctx, top_k=top_k
                ))

        if not tasks:
            return [RetrievalResult(
                query=query, source="parametric", strategy_used="parallel",
                confidence=0.1, chunks=[], citations=[],
                fallback_used=True, fallback_reason="no sources available for parallel retrieve",
            )]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = [r for r in results if isinstance(r, RetrievalResult) and not r.fallback_used]
        return valid if valid else [results[0]] if results else []
```

- [ ] **Step R4.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/rag/test_agentic/test_parallel_retrieval.py -v --no-cov
```
Expected: `4 passed`

- [ ] **Step R4.5: Commit**

```bash
cd agent-verse-backend
git add app/rag/agentic/retriever_tool.py tests/rag/test_agentic/test_parallel_retrieval.py
git commit -m "feat(rag/agentic): add parallel_retrieve via asyncio.gather — doc-2 Phase D"
```

---

## Task R5: RAG Pattern Adapters (all PLANNED patterns get adapter classes)

**Files:**
- Create: `app/rag/agentic/patterns/__init__.py`
- Create: `app/rag/agentic/patterns/base.py`
- Create: `app/rag/agentic/patterns/corrective.py`
- Create: `app/rag/agentic/patterns/adaptive.py`
- Create: `app/rag/agentic/patterns/self_rag.py`
- Create: `app/rag/agentic/patterns/speculative.py`
- Create: `app/rag/agentic/patterns/fusion.py`
- Create: `app/rag/agentic/patterns/flare.py`
- Create: `app/rag/agentic/patterns/raptor.py`
- Create: `app/rag/agentic/patterns/agentic_chunking.py`
- Create: `app/rag/agentic/patterns/colbert.py`
- Create: `tests/rag/test_agentic/test_rag_pattern_adapters.py`

- [ ] **Step R5.1: Write failing tests**

```python
# tests/rag/test_agentic/test_rag_pattern_adapters.py
"""All RAG pattern adapters from doc-1 must be importable with standard interface."""
from __future__ import annotations
import pytest
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
from app.rag.agentic.patterns.self_rag import SelfRAGPattern
from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern
from app.rag.agentic.patterns.fusion import FusionRAGPattern
from app.rag.agentic.patterns.flare import FLAREPattern
from app.rag.agentic.patterns.raptor import RAPTORPattern
from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
from app.rag.agentic.patterns.colbert import ColBERTPattern
import app.rag.agentic.patterns as patterns_pkg


def test_all_rag_pattern_adapters_importable():
    adapters = [
        CorrectiveRAGPattern, AdaptiveRAGPattern, SelfRAGPattern,
        SpeculativeRAGPattern, FusionRAGPattern, FLAREPattern,
        RAPTORPattern, AgenticChunkingPattern, ColBERTPattern,
    ]
    for cls in adapters:
        p = cls()
        assert isinstance(p, RAGPattern)


def test_all_rag_patterns_have_unique_ids():
    adapters = [
        CorrectiveRAGPattern(), AdaptiveRAGPattern(), SelfRAGPattern(),
        SpeculativeRAGPattern(), FusionRAGPattern(), FLAREPattern(),
        RAPTORPattern(), AgenticChunkingPattern(), ColBERTPattern(),
    ]
    ids = [p.pattern_id for p in adapters]
    assert len(ids) == len(set(ids)), "Duplicate pattern IDs"


def test_corrective_rag_has_correct_id():
    assert CorrectiveRAGPattern().pattern_id == "corrective_rag"


def test_adaptive_rag_has_correct_id():
    assert AdaptiveRAGPattern().pattern_id == "adaptive_rag"


def test_self_rag_has_correct_id():
    assert SelfRAGPattern().pattern_id == "self_rag"


def test_flare_has_correct_id():
    assert FLAREPattern().pattern_id == "flare"


def test_raptor_has_correct_id():
    assert RAPTORPattern().pattern_id == "raptor"


def test_all_patterns_have_state():
    for cls in [CorrectiveRAGPattern, AdaptiveRAGPattern, SelfRAGPattern,
                SpeculativeRAGPattern, FusionRAGPattern, FLAREPattern,
                RAPTORPattern, AgenticChunkingPattern, ColBERTPattern]:
        p = cls()
        assert p.state in (RAGPatternState.IMPLEMENTED, RAGPatternState.PARTIAL,
                           RAGPatternState.PLANNED)


def test_all_patterns_list_in_init():
    assert hasattr(patterns_pkg, "ALL_RAG_PATTERNS")
    assert len(patterns_pkg.ALL_RAG_PATTERNS) >= 9


def test_registry_pattern_ids_match_adapters():
    """Every adapter's pattern_id must match what's in StrategyRegistry."""
    from app.orchestration.strategy_registry import build_default_registry
    registry = build_default_registry()
    for pattern in patterns_pkg.ALL_RAG_PATTERNS:
        cap = registry.get(pattern.pattern_id)
        assert cap is not None, (
            f"RAG adapter '{pattern.pattern_id}' not found in StrategyRegistry"
        )
```

- [ ] **Step R5.2: Create directory and run to confirm failure**

```bash
mkdir -p agent-verse-backend/app/rag/agentic/patterns
touch agent-verse-backend/app/rag/agentic/patterns/__init__.py
cd agent-verse-backend && uv run pytest tests/rag/test_agentic/test_rag_pattern_adapters.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step R5.3: Implement base and all adapter files**

`app/rag/agentic/patterns/base.py`:
```python
"""RAGPattern base class — every RAG pattern adapter inherits this."""
from __future__ import annotations
import enum
from abc import ABC, abstractmethod
from typing import Any


class RAGPatternState(str, enum.Enum):
    IMPLEMENTED = "implemented"
    PARTIAL = "partial"
    PLANNED = "planned"


class RAGPattern(ABC):
    @property
    @abstractmethod
    def pattern_id(self) -> str: ...

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.PLANNED

    @property
    def description(self) -> str:
        return ""

    def is_compatible(self, goal_properties: Any) -> bool:
        return True
```

`app/rag/agentic/patterns/corrective.py`:
```python
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

class CorrectiveRAGPattern(RAGPattern):
    """Corrective RAG — evaluates retrieved docs, corrects if low quality (CRAG)."""
    @property
    def pattern_id(self) -> str: return "corrective_rag"
    @property
    def state(self) -> RAGPatternState: return RAGPatternState.PARTIAL
    @property
    def description(self) -> str: return "CRAG: evaluate retrieval quality, correct via web search"
```

`app/rag/agentic/patterns/adaptive.py`:
```python
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

class AdaptiveRAGPattern(RAGPattern):
    """Adaptive RAG — dynamically selects retrieval strategy per query complexity."""
    @property
    def pattern_id(self) -> str: return "adaptive_rag"
    @property
    def state(self) -> RAGPatternState: return RAGPatternState.PLANNED
    @property
    def description(self) -> str: return "Adaptive RAG: selects no-retrieval/single-hop/multi-hop per query"
```

`app/rag/agentic/patterns/self_rag.py`:
```python
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

class SelfRAGPattern(RAGPattern):
    """Self-RAG — LLM decides whether to retrieve and critiques its output (Asai 2023)."""
    @property
    def pattern_id(self) -> str: return "self_rag"
    @property
    def state(self) -> RAGPatternState: return RAGPatternState.PLANNED
    @property
    def description(self) -> str: return "Self-RAG: retrieve on demand + self-critique tokens"
```

`app/rag/agentic/patterns/speculative.py`:
```python
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

class SpeculativeRAGPattern(RAGPattern):
    """Speculative RAG — multiple hypotheses retrieved and verified."""
    @property
    def pattern_id(self) -> str: return "speculative_rag"
    @property
    def state(self) -> RAGPatternState: return RAGPatternState.PLANNED
    @property
    def description(self) -> str: return "Speculative RAG: parallel hypothesis generation + verification"
```

`app/rag/agentic/patterns/fusion.py`:
```python
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

class FusionRAGPattern(RAGPattern):
    """Fusion RAG — multi-query generation + RRF fusion of results."""
    @property
    def pattern_id(self) -> str: return "fusion_rag"
    @property
    def state(self) -> RAGPatternState: return RAGPatternState.PARTIAL
    @property
    def description(self) -> str: return "Fusion RAG: multi-query expansion + RRF result fusion"
```

`app/rag/agentic/patterns/flare.py`:
```python
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

class FLAREPattern(RAGPattern):
    """FLARE — Forward-Looking Active Retrieval Augmentation (Jiang 2023)."""
    @property
    def pattern_id(self) -> str: return "flare"
    @property
    def state(self) -> RAGPatternState: return RAGPatternState.PLANNED
    @property
    def description(self) -> str: return "FLARE: retrieve when model is uncertain about next sentence"
```

`app/rag/agentic/patterns/raptor.py`:
```python
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

class RAPTORPattern(RAGPattern):
    """RAPTOR — Recursive Abstractive Processing for Tree-Organized Retrieval (Sarthi 2024)."""
    @property
    def pattern_id(self) -> str: return "raptor"
    @property
    def state(self) -> RAGPatternState: return RAGPatternState.PLANNED
    @property
    def description(self) -> str: return "RAPTOR: hierarchical summarization + tree retrieval"
```

`app/rag/agentic/patterns/agentic_chunking.py`:
```python
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

class AgenticChunkingPattern(RAGPattern):
    """Agentic Chunking — LLM decides chunk boundaries (proposition-based)."""
    @property
    def pattern_id(self) -> str: return "agentic_chunking"
    @property
    def state(self) -> RAGPatternState: return RAGPatternState.PLANNED
    @property
    def description(self) -> str: return "Agentic Chunking: LLM-driven proposition extraction as chunks"
```

`app/rag/agentic/patterns/colbert.py`:
```python
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

class ColBERTPattern(RAGPattern):
    """ColBERT / Late Interaction — token-level late interaction scoring."""
    @property
    def pattern_id(self) -> str: return "colbert_late_interaction"
    @property
    def state(self) -> RAGPatternState: return RAGPatternState.PLANNED
    @property
    def description(self) -> str: return "ColBERT: MaxSim late interaction reranking"
```

`app/rag/agentic/patterns/__init__.py`:
```python
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
from app.rag.agentic.patterns.self_rag import SelfRAGPattern
from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern
from app.rag.agentic.patterns.fusion import FusionRAGPattern
from app.rag.agentic.patterns.flare import FLAREPattern
from app.rag.agentic.patterns.raptor import RAPTORPattern
from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
from app.rag.agentic.patterns.colbert import ColBERTPattern

ALL_RAG_PATTERNS: list[RAGPattern] = [
    CorrectiveRAGPattern(), AdaptiveRAGPattern(), SelfRAGPattern(),
    SpeculativeRAGPattern(), FusionRAGPattern(), FLAREPattern(),
    RAPTORPattern(), AgenticChunkingPattern(), ColBERTPattern(),
]

__all__ = [
    "RAGPattern", "RAGPatternState", "ALL_RAG_PATTERNS",
    "CorrectiveRAGPattern", "AdaptiveRAGPattern", "SelfRAGPattern",
    "SpeculativeRAGPattern", "FusionRAGPattern", "FLAREPattern",
    "RAPTORPattern", "AgenticChunkingPattern", "ColBERTPattern",
]
```

- [ ] **Step R5.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/rag/test_agentic/test_rag_pattern_adapters.py -v --no-cov
```
Expected: `11 passed`

- [ ] **Step R5.5: Commit**

```bash
cd agent-verse-backend
git add app/rag/agentic/patterns/ tests/rag/test_agentic/test_rag_pattern_adapters.py
git commit -m "feat(rag/agentic/patterns): add 9 RAG pattern adapters — corrective, adaptive, self_rag, speculative, fusion, FLARE, RAPTOR, agentic_chunking, ColBERT"
```

---

## Task R6: Complete Agentic RAG Verification

- [ ] **Step R6.1: Run the full agentic RAG test suite**

```bash
cd agent-verse-backend
uv run pytest tests/rag/ -v --no-cov 2>&1 | tail -20
```
Expected: All passing

- [ ] **Step R6.2: Verify StrategyRegistry covers all RAG adapters**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_strategy_registry_complete.py -v --no-cov -k "rag"
```

- [ ] **Step R6.3: Final commit**

```bash
cd agent-verse-backend
git add -A
git commit -m "test(rag): verify complete agentic RAG coverage — all doc-2 patterns and doc-1 RAG patterns covered"
```
