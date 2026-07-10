# Memory, Embeddings, and Prompt Builder

**Date:** 2026-07-08

## Purpose

This document explains how AgentVerse remembers past work, chooses embedding models, constructs prompts, and uses context to make agent execution more reliable.

## Agent Memory Types

AgentVerse has multiple memory layers.

```text
Current goal context
  -> AgentState.context

Past successful/failed executions
  -> ExecutionMemory

Cross-session semantic learnings
  -> LongTermMemoryStore

Failure lessons
  -> ReflexionStore

RPA/browser visual memory
  -> LongTermMemory with rpa_extraction / rpa_vision tags
```

## Current Goal Context

Stored in `AgentState.context`.

Examples:

```python
context = {
    "tool_context": ToolContext(...),
    "tool_prompt": "Available tools...",
    "system_prompt": "agent-specific instructions",
    "rag_knowledge": "retrieved context",
    "rag_citations": [...],
    "image_context": "visual analysis",
    "total_cost_usd": 0.012,
    "_feedback_history": [...],
}
```

This context lives only for a single goal run.

## Execution Memory

Implemented in `app/memory/execution.py`.

Stores:

- successful plans
- failed approaches
- failure errors

Successful plan storage:

```python
ExecutionMemory.record_async(
    goal="List recent Jira issues",
    plan=["Call jira_search_issues..."],
    success=True,
    tenant_id=tenant_id,
    db=db_factory,
)
```

Recall path:

```text
Goal arrives
  -> _node_rag_retrieval
  -> exec_memory.recall_async(goal_hint)
  -> injects "Past winning plans" into planner prompt
```

Use case:

```text
If Jira goals succeeded before with "project is not EMPTY ORDER BY updated DESC",
future Jira goals reuse that pattern.
```

## Long-Term Memory

Implemented in `app/memory/long_term.py`.

Stores `LongTermMemory` records:

```python
LongTermMemory(
    content="Goal: X -> Result: Y",
    source_goal_id="...",
    memory_type="success_pattern" | "tool_preference" | "domain_fact" | "failure_pattern" | "rpa_extraction",
    confidence=0.8,
    tags=[...],
)
```

When embedder is available, memory is stored with pgvector embedding:

```text
memory.content -> EmbedRequest -> vector -> long_term_memory.embedding
```

Recall uses semantic search:

```sql
ORDER BY embedding <=> CAST(:qvec AS vector)
```

## RPA and Vision Memory

When the agent uses `rpa_extract_text` or `rpa_screenshot`, extracted text or screenshot analysis is stored via:

```python
LongTermMemoryStore.store_rpa_extraction(...)
```

Tags:

```text
rpa
vision
screenshot-analysis
web-extraction
```

This lets future browser automation goals remember what previous pages looked like.

## Reflexion Memory

Implemented in `app/agent/patterns/reflexion.py`.

Failure feedback is converted into lessons:

```text
"For goal 'Search Jira': avoid bare ORDER BY; use project is not EMPTY ORDER BY updated DESC."
```

Then future goals recall these lessons and inject them into planning context.

## Embedding System

Core request/response types live in `app/providers/base.py`:

```python
EmbedRequest(texts=[...], model="...")
EmbedResponse(embeddings=[[...]], model="...")
```

## Embedding Router

Implemented in `app/embedding/router.py`.

Built-in models:

| Model | Dimension | Cost Class / Use |
|---|---:|---|
| OpenAI `text-embedding-3-large` | 3072 | highest precision |
| OpenAI `text-embedding-3-small` | 1536 | default |
| Voyage `voyage-3-large` | 1024 | RAG optimized |
| Voyage `voyage-3-lite` | 512 | low cost |
| Gemini `text-embedding-004` | 768 | Gemini stack |
| lexical fallback | 384 | no provider available |

## Embedding Orchestrator

Implemented in `app/embedding/orchestrator.py`.

Maps content type to embedding modality:

```text
TEXT -> text
CODE -> code, then text fallback
IMAGE -> multimodal, image, text fallback
AUDIO -> text transcript
VIDEO -> multimodal, text fallback
PDF -> text
CSV/JSON -> text
```

Also respects tenant plan:

```text
free/starter -> free, low cost models
professional -> free, low, medium
enterprise -> free, low, medium, high
```

## Dimension Policy

Implemented in `app/embedding/dimension_policy.py`.

Maps model IDs to vector dimensions:

```text
text-embedding-3-small -> 1536
text-embedding-3-large -> 3072
voyage-3-lite -> 1024
voyage-code-3 -> 1024
voyage-multimodal-3 -> 1024
fake-embedding -> 10
```

## Drift and Re-Embedding

`EmbeddingDriftMonitor` measures degradation:

```text
avg_similarity >= 0.85 -> stable
>= 0.70 -> low drift
>= 0.55 -> medium
>= 0.40 -> high
else -> critical
```

`ReembeddingPolicy` triggers re-embedding when:

```text
model changed
dimension mismatch
collection is stale
```

## Prompt Builder

The planner prompt is assembled in `AgentGraph._node_plan`.

Context layers:

1. Agent system prompt
2. RAG context
3. Knowledge base context
4. Tool prompt
5. Full tool schema block
6. Tool call format reminder
7. Civilization blackboard
8. Visual/image context
9. Previous verifier feedback
10. Chain-of-thought context if available
11. Selected skills

Prompt example:

```text
System: PLANNER_SYSTEM + agent system prompt

Goal: List recent Jira issues

[Available connector tools]
- jira_search_issues ... input_schema {...}

[Relevant context]
Past winning plan: use project is not EMPTY ORDER BY updated DESC

[Previous attempt feedback]
JQL returned 0 issues; use project restriction
```

## Tool Schema Injection

Implemented in `app/mcp/tool_intelligence.py`.

The schema injector gives the LLM exact tool names and parameter schemas:

```text
jira_search_issues
  required: jql
  optional: max_results, fields
```

This prevents hallucinated parameters like `jql_query` or wrong tool names.

## Skill Selector

Implemented in `app/agent/skill_selector.py`.

Selects compact skills based on trigger hints:

```text
report -> structured-reporting
review code -> code-review
summarize -> summarize-and-compress
research -> web-research
```

Skills reduce prompt size and narrow tool choices.
