# Community 330

> 20 nodes · cohesion 0.16

## Key Concepts

- **LLMResponseCache** (17 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **.get()** (6 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **llm_response_cache.py** (5 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **.set()** (5 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **LLMCacheEntry** (4 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **._l1_put()** (4 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **._get_stats()** (3 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **._inc()** (3 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **._make_key()** (3 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **.stats()** (3 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **.clear()** (2 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **.should_skip_cache()** (2 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **Any** (2 connections)
- **LLM Response Cache ================== Caches complete LLM completion responses…** (1 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **Cache an LLM response. Silently ignores all errors.** (1 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **Clear all cached entries for a tenant.** (1 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **Return True when the request should NOT be cached. Conditions: - Contains…** (1 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **Per-tenant LLM response cache backed by Redis with in-process L1 dict. Usage:…** (1 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`
- **Return cached LLM response or None on miss.** (1 connections) — `agent-verse-backend/app/rag/llm_response_cache.py`

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 435](Community_435.md) (1 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (1 shared connections)
- [Community 102](Community_102.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/rag/llm_response_cache.py`

## Audit Trail

- EXTRACTED: 35 (95%)
- INFERRED: 2 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*