# Federated RAG Search

> 140 nodes · cohesion 0.04

## Key Concepts

- **rag/gateway.py** (88 connections) — `agent-verse-backend/app/rag/gateway.py`
- **rag/contracts.py** (78 connections) — `agent-verse-backend/app/rag/contracts.py`
- **RetrievalStrategyExecutionError** (40 connections) — `agent-verse-backend/app/rag/engine.py`
- **RAGExecutionRequest** (39 connections) — `agent-verse-backend/app/rag/contracts.py`
- **RAGExecutionResult** (39 connections) — `agent-verse-backend/app/rag/contracts.py`
- **execute_core_strategy()** (38 connections) — `agent-verse-backend/app/rag/gateway.py`
- **RAGStrategyTrace** (35 connections) — `agent-verse-backend/app/rag/contracts.py`
- **self_rag.py** (31 connections) — `agent-verse-backend/app/rag/agentic/patterns/self_rag.py`
- **patterns/modular.py** (30 connections) — `agent-verse-backend/app/rag/agentic/patterns/modular.py`
- **agentic.py** (29 connections) — `agent-verse-backend/app/rag/agentic/patterns/agentic.py`
- **flare.py** (28 connections) — `agent-verse-backend/app/rag/agentic/patterns/flare.py`
- **speculative.py** (26 connections) — `agent-verse-backend/app/rag/agentic/patterns/speculative.py`
- **_canonical_result()** (24 connections) — `agent-verse-backend/app/rag/gateway.py`
- **_embed_text()** (20 connections) — `agent-verse-backend/app/rag/gateway.py`
- **_search_persisted()** (19 connections) — `agent-verse-backend/app/rag/gateway.py`
- **.execute()** (18 connections) — `agent-verse-backend/app/rag/agentic/patterns/agentic.py`
- **patterns/raft.py** (18 connections) — `agent-verse-backend/app/rag/agentic/patterns/raft.py`
- **_CoreRAGRuntimeAdapter** (18 connections) — `agent-verse-backend/app/rag/contracts.py`
- **_extend_trace()** (18 connections) — `agent-verse-backend/app/rag/gateway.py`
- **.execute()** (16 connections) — `agent-verse-backend/app/rag/agentic/patterns/self_rag.py`
- **.execute()** (15 connections) — `agent-verse-backend/app/rag/agentic/patterns/flare.py`
- **corrective.py** (14 connections) — `agent-verse-backend/app/rag/agentic/patterns/corrective.py`
- **_ReasoningRAGRuntimeAdapter** (14 connections) — `agent-verse-backend/app/rag/contracts.py`
- **ColBERTRAGRuntimeAdapter** (12 connections) — `agent-verse-backend/app/rag/agentic/patterns/colbert.py`
- **merge_grounding_results()** (12 connections) — `agent-verse-backend/app/rag/engine.py`
- *... and 115 more nodes in this community*

## Relationships

- [RAG Capability Catalogue](RAG_Capability_Catalogue.md) (66 shared connections)
- [Adaptive RAG Pattern](Adaptive_RAG_Pattern.md) (45 shared connections)
- [Community 50](Community_50.md) (37 shared connections)
- [Community 55](Community_55.md) (31 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (26 shared connections)
- [Community 76](Community_76.md) (23 shared connections)
- [ColBERT Reranking](ColBERT_Reranking.md) (20 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (11 shared connections)
- [Community 167](Community_167.md) (11 shared connections)
- [Agent Pattern Adapters (AutoGPT/BabyAGI/CodeAct)](Agent_Pattern_Adapters_AutoGPT-BabyAGI-CodeAct.md) (11 shared connections)
- [Knowledge Ingestion API](Knowledge_Ingestion_API.md) (9 shared connections)
- [Community 255](Community_255.md) (7 shared connections)

## Source Files

- `agent-verse-backend/app/knowledge/federated_search.py`
- `agent-verse-backend/app/rag/agentic/patterns/agentic.py`
- `agent-verse-backend/app/rag/agentic/patterns/colbert.py`
- `agent-verse-backend/app/rag/agentic/patterns/corrective.py`
- `agent-verse-backend/app/rag/agentic/patterns/flare.py`
- `agent-verse-backend/app/rag/agentic/patterns/graph.py`
- `agent-verse-backend/app/rag/agentic/patterns/modular.py`
- `agent-verse-backend/app/rag/agentic/patterns/raft.py`
- `agent-verse-backend/app/rag/agentic/patterns/self_rag.py`
- `agent-verse-backend/app/rag/agentic/patterns/speculative.py`
- `agent-verse-backend/app/rag/agentic/patterns/web_augmented.py`
- `agent-verse-backend/app/rag/contracts.py`
- `agent-verse-backend/app/rag/engine.py`
- `agent-verse-backend/app/rag/gateway.py`
- `agent-verse-backend/app/runtime_readiness/degraded_mode_policy.py`

## Audit Trail

- EXTRACTED: 704 (97%)
- INFERRED: 21 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*