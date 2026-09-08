# Community 453

> 14 nodes · cohesion 0.22

## Key Concepts

- **LLMQueryTransformer** (12 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`
- **._call()** (6 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`
- **.transform()** (6 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`
- **.decompose()** (5 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`
- **.rewrite()** (4 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`
- **.step_back()** (4 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`
- **._parse_lines()** (3 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`
- **Return [rewritten_query] or [original] on failure.** (1 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`
- **Apply all strategies and return a de-duplicated union of queries.** (1 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`
- **Transforms queries using an LLM to improve retrieval recall and precision.** (1 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`
- **Extract non-empty, non-boilerplate lines from LLM output.** (1 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`
- **Return [original_query, step_back_query].** (1 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`
- **Return [original_query, sub_q1, sub_q2, …] (de-duplicated).** (1 connections) — `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`

## Relationships

- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (4 shared connections)
- [Adaptive RAG Pattern](Adaptive_RAG_Pattern.md) (2 shared connections)
- [Community 55](Community_55.md) (1 shared connections)
- [Community 512](Community_512.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/rag/agentic/llm_query_transformer.py`

## Audit Trail

- EXTRACTED: 26 (93%)
- INFERRED: 2 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*