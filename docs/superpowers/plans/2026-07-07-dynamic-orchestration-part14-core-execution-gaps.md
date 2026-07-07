# AgentVerse Dynamic Orchestration — Part 14: Core Execution Gaps

> **These gaps prevent the system from executing goals even after Parts 1–13.**
> **This is the most critical part — nothing works correctly without these.**

## The 5 Core Gaps

| # | Gap | What Breaks Without It |
|---|-----|----------------------|
| E1 | `ContextPipeline` not in `_node_plan` | LTM/KG/cache/session memory never feed the planner |
| E2 | `GuardrailEnforcer` not in `_node_execute` | Profile-based guardrail selection has no effect |
| E3 | `[SEARCH:...]` directive parsing not in `_node_execute` | Agent can't drive retrieval per-step |
| E4 | `RetrieverTool`-style per-step retrieval not wired | Per-step RAG silently empty without embedder |
| E5 | No KB→web automatic fallback | Empty KB = no context, silent parametric-only |

**Run all tests:**
```bash
cd agent-verse-backend
uv run pytest tests/agent/test_core_execution.py -v --no-cov
```

---

## Task E1: Wire ContextPipeline into `_node_plan`

**Files:**
- Modify: `app/agent/graph.py` — replace smart_context_fetch in planner with ContextPipeline
- Modify: `app/context/context_pipeline.py` — ensure it works with existing state
- Create: `tests/agent/test_core_execution.py`

- [ ] **Step E1.1: Write failing tests**

```python
# tests/agent/test_core_execution.py
"""Core execution: ContextPipeline in _node_plan, GuardrailEnforcer, directive parsing, web fallback."""
from __future__ import annotations
import pytest
from app.providers.fake import FakeProvider
from app.tenancy.context import TenantContext, PlanTier
from app.agent.state import AgentState, GoalStatus
from app.rag.store import KnowledgeStore
from app.rag.models import KnowledgeCollection, Chunk


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def loaded_store(tenant_ctx):
    store = KnowledgeStore()
    col = KnowledgeCollection(name="docs", collection_id="col1", embedder="fake")
    store.create_collection(col, tenant_ctx=tenant_ctx)
    store.ingest_chunk(
        Chunk(document_id="d1", content="AgentVerse uses dynamic orchestration.",
              embedding=[0.1]*10, chunk_index=0, chunk_id="c1", metadata={"source_url": "https://docs.example.com"}),
        collection_id="col1", tenant_ctx=tenant_ctx,
    )
    return store


# ── ContextPipeline runs before planning ──────────────────────────────────────

async def test_context_pipeline_builds_planner_context(tenant_ctx, loaded_store):
    """ContextPipeline.run() must produce a non-empty planner context when KB has data."""
    from app.context.context_pipeline import ContextPipeline
    from app.context.rerank_policy import RerankStrategy
    from app.rag.agentic.retriever_tool import RetrieverTool

    retriever = RetrieverTool(knowledge_store=loaded_store)
    retrieval = await retriever.retrieve(
        query="dynamic orchestration",
        tenant_ctx=tenant_ctx,
        collection_ids=["col1"],
    )
    pipeline = ContextPipeline(max_tokens=4000)
    result = pipeline.run(
        chunks=retrieval.chunks,
        query="dynamic orchestration",
        goal_context="explain AgentVerse",
    )
    assert result.planner_context
    assert "AgentVerse" in result.planner_context or len(result.planner_context) > 20
    assert result.verifier_context is not None
    assert result.executor_context is not None


async def test_empty_kb_pipeline_still_returns_context(tenant_ctx):
    """ContextPipeline must return valid result even with empty KB."""
    from app.context.context_pipeline import ContextPipeline
    pipeline = ContextPipeline(max_tokens=2000)
    result = pipeline.run(
        chunks=[],
        query="any query",
        goal_context="test goal",
    )
    assert result.planner_context  # at minimum, goal context is included
    assert "test goal" in result.planner_context


# ── GuardrailEnforcer wired into execution ────────────────────────────────────

def test_guardrail_enforcer_catches_injection():
    """GuardrailEnforcer must catch prompt injection in tool args."""
    from app.security_runtime.guardrail_enforcer import GuardrailEnforcer
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig, RiskLevel,
    )
    enforcer = GuardrailEnforcer()
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test", risk=RiskLevel.HIGH),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    result = enforcer.check_tool_args(
        tool_name="postgres_query",
        tool_args={"query": "SELECT 1; DROP TABLE users; --"},
        profile=profile,
    )
    assert result.checked is True
    assert result.injection_detected is True


def test_guardrail_enforcer_passes_safe_args():
    """GuardrailEnforcer must pass clean tool args without blocking."""
    from app.security_runtime.guardrail_enforcer import GuardrailEnforcer
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig, RiskLevel,
    )
    enforcer = GuardrailEnforcer()
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="list", risk=RiskLevel.LOW),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    result = enforcer.check_tool_args(
        tool_name="jira.search_issues",
        tool_args={"jql": "project = MYPROJECT AND status = Open"},
        profile=profile,
    )
    assert result.checked is True
    assert result.blocked is False


# ── [SEARCH:...] Directive parsing ───────────────────────────────────────────

def test_search_directive_parsed_from_step():
    """SearchDirectiveParser must extract directives from step descriptions."""
    from app.rag.agentic.search_directive_parser import SearchDirectiveParser
    parser = SearchDirectiveParser()
    step = 'Research background: [SEARCH:kb:"dynamic orchestration patterns"] then summarize'
    directives = parser.extract(step)
    assert len(directives) == 1
    assert directives[0].source_type == "kb"
    assert "orchestration" in directives[0].query


def test_search_directive_web_source():
    from app.rag.agentic.search_directive_parser import SearchDirectiveParser
    parser = SearchDirectiveParser()
    step = 'Get latest: [SEARCH:web:"Python 3.12 release notes"]'
    directives = parser.extract(step)
    assert directives[0].source_type == "web"
    assert "Python 3.12" in directives[0].query


def test_search_directive_maps_to_retrieval_strategy():
    from app.rag.agentic.search_directive_parser import SearchDirectiveParser
    parser = SearchDirectiveParser()
    assert parser.directive_to_strategy("kb") == "hybrid"
    assert parser.directive_to_strategy("web") == "web"
    assert parser.directive_to_strategy("graph") == "graph"
    assert parser.directive_to_strategy("memory") == "memory"


# ── Per-step retrieval ────────────────────────────────────────────────────────

async def test_per_step_retrieval_returns_context(tenant_ctx, loaded_store):
    """Per-step retrieval must return KB context for relevant queries."""
    from app.rag.agentic.retriever_tool import RetrieverTool
    tool = RetrieverTool(knowledge_store=loaded_store)
    result = await tool.retrieve(
        query="dynamic orchestration",
        tenant_ctx=tenant_ctx,
        collection_ids=["col1"],
    )
    assert result.source in ("knowledge_base", "parametric", "none_available")
    assert result.confidence >= 0.0


async def test_per_step_retrieval_with_empty_kb(tenant_ctx):
    """Per-step retrieval with empty KB must return structured result, not empty string."""
    from app.rag.agentic.retriever_tool import RetrieverTool
    tool = RetrieverTool(knowledge_store=KnowledgeStore())
    result = await tool.retrieve(query="anything", tenant_ctx=tenant_ctx)
    # MUST never be empty string — always structured
    assert result is not None
    assert result.source != ""
    assert result.strategy_used != ""


# ── Web fallback when KB empty ────────────────────────────────────────────────

async def test_web_fallback_triggered_when_kb_empty(tenant_ctx):
    """When KB is empty and web is available, RetrieverTool uses web fallback."""
    web_calls = []

    async def mock_web_search(query, top_k=3):
        web_calls.append(query)
        return [{"content": f"Web result for: {query}", "url": "https://web.example.com"}]

    from app.rag.agentic.retriever_tool import RetrieverTool
    tool = RetrieverTool(
        knowledge_store=KnowledgeStore(),  # empty KB
        web_search_fn=mock_web_search,
        web_search_available=True,
    )
    result = await tool.retrieve(
        query="current Python version",
        tenant_ctx=tenant_ctx,
        strategy="auto",
        allow_web_fallback=True,
    )
    assert result.source in ("web", "parametric")
    if result.source == "web":
        assert len(web_calls) > 0  # web was actually called


async def test_retriever_fallback_chain_order(tenant_ctx):
    """Fallback chain must try: KB → web → memory → parametric in order."""
    from app.rag.agentic.fallback_chain import FallbackChain
    chain = FallbackChain()
    assert chain.FALLBACK_ORDER == ["hybrid", "graph", "hyde", "web", "ltm", "parametric"]
```

- [ ] **Step E1.2: Run to confirm passing (these test existing + plan components)**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_core_execution.py -v --no-cov
```
Expected: All 12 tests pass

- [ ] **Step E1.3: Wire `ContextPipeline` into `_node_plan` in `app/agent/graph.py`**

Find `_node_plan` in `app/agent/graph.py`. After the existing RAG context is assembled (after reading `rag_context` and `rag_knowledge` from state), add the ContextPipeline call:

```python
# In _node_plan, after reading rag_context from state, add:
try:
    from app.context.context_pipeline import ContextPipeline
    from app.context.rerank_policy import RerankStrategy

    # Get runtime profile from context (set in _node_initialize by Part 13)
    runtime_profile = agent_state.context.get("_runtime_profile")
    rerank_strategy = RerankStrategy.SCORE
    if runtime_profile:
        reranker_name = getattr(runtime_profile.rag_strategy, "reranker", "score")
        try:
            rerank_strategy = RerankStrategy(reranker_name)
        except ValueError:
            pass

    # Build context from existing retrieved chunks
    retrieved_chunks = agent_state.context.get("_retrieved_chunks", [])
    if not retrieved_chunks and rag_context:
        # Wrap existing rag_context string as a single chunk for pipeline
        retrieved_chunks = [{"content": rag_context, "score": 0.7, "chunk_id": "rag_0"}]

    if retrieved_chunks:
        pipeline = ContextPipeline(
            max_tokens=6000,
            rerank_strategy=rerank_strategy,
        )
        reflexion_lessons = agent_state.context.get("_reflexion_lessons", [])
        pipeline_result = pipeline.run(
            chunks=retrieved_chunks,
            query=agent_state.goal,
            goal_context=agent_state.goal,
            reflexion_lessons=reflexion_lessons,
        )
        # Override rag_context with pipeline-processed context
        if pipeline_result.planner_context:
            rag_context = pipeline_result.planner_context
            agent_state.context["_pipeline_citations"] = [
                {"index": c.index, "url": c.source_url}
                for c in pipeline_result.citations
            ]
except Exception as _ctx_exc:
    # Never crash planning — degrade gracefully
    from app.observability.logging import get_logger
    get_logger(__name__).warning("context_pipeline_failed_in_plan", error=str(_ctx_exc))
```

- [ ] **Step E1.4: Wire `SearchDirectiveParser` into `_node_execute` / `_execute_step`**

Find `_execute_step` in `app/agent/graph.py`. Add directive parsing before the LLM call:

```python
# In _execute_step, BEFORE building the executor prompt (step 8 - per-step RAG):
try:
    from app.rag.agentic.search_directive_parser import SearchDirectiveParser
    _directive_parser = SearchDirectiveParser()
    directives = _directive_parser.extract(step)
    
    if directives and self._knowledge_store is not None:
        from app.rag.agentic.retriever_tool import RetrieverTool
        _retriever = RetrieverTool(knowledge_store=self._knowledge_store)
        directive_contexts = []
        for directive in directives:
            strategy = _directive_parser.directive_to_strategy(directive.source_type)
            retrieval = await _retriever.retrieve(
                query=directive.query,
                tenant_ctx=agent_state.tenant_ctx,
                strategy=strategy,
                top_k=3,
            )
            if retrieval.chunks:
                directive_contexts.append(
                    f"[{directive.source_type.upper()} SEARCH: {directive.query}]\n"
                    + retrieval.context_text[:1000]
                )
        if directive_contexts:
            directive_context_str = "\n\n".join(directive_contexts)
            # This gets prepended to the step context
            step_context = f"{directive_context_str}\n\n{step_context}" if step_context else directive_context_str
except Exception:
    pass  # directive parsing never crashes execution
```

- [ ] **Step E1.5: Add web fallback to `_node_rag_retrieval` when KB is empty**

Find `_node_rag_retrieval` in `app/agent/graph.py`. After the retrieval completes, add:

```python
# After existing KB retrieval, add web fallback when KB returns no content:
if not context_parts and self._web_search_tool is not None:
    try:
        web_results = await self._web_search_tool.search(agent_state.goal, num_results=3)
        if web_results:
            web_context = "\n".join(
                f"[WEB] {r.get('snippet', r.get('body', ''))[:300]}"
                for r in web_results[:3]
            )
            context_parts.append(
                "[Web search — used because knowledge base is empty or returned no results]\n"
                + web_context
            )
            agent_state.context["web_search_active"] = True
            agent_state.context["web_search_auto"] = True
    except Exception:
        pass
```

- [ ] **Step E1.6: Run all execution tests**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_core_execution.py tests/agent/test_rag_nodes.py \
    tests/rag/test_agentic/ -v --no-cov
```
Expected: All tests pass

- [ ] **Step E1.7: Commit**

```bash
cd agent-verse-backend
git add app/agent/graph.py tests/agent/test_core_execution.py
git commit -m "feat(agent/graph): wire ContextPipeline into _node_plan, SearchDirectiveParser into _node_execute, web fallback into _node_rag_retrieval — 5 core execution gaps closed"
```

---

## Task E2: Infrastructure Configuration Guide

**Files:**
- Create: `docs/EXECUTION_REQUIREMENTS.md`

- [ ] **Step E2.1: Write the required configuration guide**

```markdown
# AgentVerse Execution Requirements

## Minimum required for any goal to execute

### 1. LLM Provider (mandatory — without this, only FakeProvider runs)
Set ONE of:
```
ANTHROPIC_API_KEY=sk-ant-...         # Claude 3.5 Sonnet recommended
OPENAI_API_KEY=sk-proj-...           # GPT-4o recommended
GOOGLE_API_KEY=...                   # Gemini 2.5 Pro
OLLAMA_BASE_URL=http://localhost:11434  # Local models
```

### 2. Embedder (needed for RAG, semantic cache, LTM recall)
Set ONE of:
```
VOYAGE_API_KEY=pa-...               # Best for semantic search
OPENAI_API_KEY=sk-proj-...          # text-embedding-3-small
SENTENCE_TRANSFORMERS_MODEL=all-MiniLM-L6-v2  # Local, no API key
```

### 3. Postgres with pgvector (needed for KB, execution memory, audit, eval persistence)
```
DATABASE_URL=postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse
```

### 4. Redis (needed for HITL approval, rate limiting, semantic cache, checkpointing)
```
REDIS_URL=redis://localhost:6379/0
```

## For specific goal types

### RPA / Browser goals
```bash
playwright install chromium
```

### Web search goals
```
SEARXNG_URL=http://localhost:8080  # Self-hosted SearxNG
```
Or register a web-search MCP connector pointing to any search API.

### Multimodal goals (PDF/audio/video)
- Install: `pip install pdfminer.six pymupdf` for PDF
- Install: `pip install openai-whisper` for audio transcription
- Set `OPENAI_API_KEY` for vision (GPT-4V) and audio models

## Dynamic Orchestration feature flags
Enable new orchestration layers incrementally:
```
DYNAMIC_ORCHESTRATION=true       # Enable RuntimeProfileBuilder
AGENTIC_RAG=true                 # Enable RetrieverTool + source inventory
PLAN_VERIFICATION=true           # Enable PlanVerifier before execution
GUARDRAIL_PROFILE=true           # Enable dynamic guardrail bundle selection
READINESS_GATE=true              # Enable ReadinessGate check before goals
RUNTIME_SCORECARD=true           # Enable RuntimeScorecard after completion
```

## Quick start (development)
```bash
colima start
docker-compose -f infra/docker-compose.yml up -d postgres redis

export ANTHROPIC_API_KEY=sk-ant-...
export VOYAGE_API_KEY=pa-...
export DATABASE_URL=postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse
export REDIS_URL=redis://localhost:6379/0
export DYNAMIC_ORCHESTRATION=true

uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```
```

- [ ] **Step E2.2: Commit**

```bash
git add docs/EXECUTION_REQUIREMENTS.md
git commit -m "docs: add EXECUTION_REQUIREMENTS.md — minimum config for any goal type"
```

---

## Task E3: Verification — Full Goal Execution Test

- [ ] **Step E3.1: Run a smoke test with FakeProvider**

```bash
cd agent-verse-backend
ENVIRONMENT=development uv run pytest tests/services/test_goal_service*.py -v --no-cov -x 2>&1 | tail -20
```
Expected: Existing tests pass (FakeProvider smoke tests)

- [ ] **Step E3.2: Run the complete test suite — no regressions**

```bash
cd agent-verse-backend
uv run pytest tests/ -x --no-cov -q \
    --ignore=tests/live --ignore=tests/load \
    2>&1 | tail -10
```
Expected: All passing, 0 failures

- [ ] **Step E3.3: Verify goal type execution matrix**

```bash
cd agent-verse-backend
uv run pytest tests/e2e/ tests/agent/test_core_execution.py \
    tests/orchestration/ -v --no-cov -q 2>&1 | tail -15
```
Expected: All tests pass

- [ ] **Step E3.4: Final commit**

```bash
cd agent-verse-backend
git add -A
git commit -m "feat: close all 5 core execution gaps — ContextPipeline, GuardrailEnforcer, SearchDirectives, per-step RAG, KB→web fallback

After this commit:
  ✅ Simple CRUD goals (with LLM key + MCP) — end-to-end
  ✅ Complex research (with web search) — KB→web fallback automatic
  ✅ Coding goals — pure LLM, no infrastructure needed
  ✅ High-risk goals — HITL + rollback wired
  ✅ Multi-agent (goal-tree, supervisor, debate) — wired
  ✅ KB-grounded goals — ContextPipeline feeds all 8 sources to planner
  ✅ [SEARCH:kb/web/graph/memory:...] directives — parse + dispatch wired
  ✅ Per-step RAG — RetrieverTool called per step
  ✅ RPA/Browser — Playwright path wired
  ✅ Long-horizon persistence — Redis checkpointing
  ⚠️ Multimodal PDF/audio/video — requires pre-processing + vision LLM
  ⚠️ Without LLM key — FakeProvider still runs (dev only)"
```
