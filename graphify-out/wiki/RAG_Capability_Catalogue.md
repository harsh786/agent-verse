# RAG Capability Catalogue

> 134 nodes · cohesion 0.03

## Key Concepts

- **RAGStrategy (enum, 18 strategies)** (88 connections) — `agent-verse-backend/app/rag/contracts.py`
- **RetrievalGateway** (33 connections) — `agent-verse-backend/app/rag/gateway.py`
- **.execute()** (23 connections) — `agent-verse-backend/app/rag/gateway.py`
- **retriever.py** (23 connections) — `agent-verse-backend/app/rag_platform/retriever.py`
- **catalogue.py** (21 connections) — `agent-verse-backend/app/rag/catalogue.py`
- **.readiness_context()** (20 connections) — `agent-verse-backend/app/rag/gateway.py`
- **rag/__init__.py** (18 connections) — `agent-verse-backend/app/rag/__init__.py`
- **RAGRetriever (rag_platform synthesis layer)** (16 connections) — `agent-verse-backend/app/rag_platform/retriever.py`
- **RAGRuntimeDependency** (15 connections) — `agent-verse-backend/app/rag/catalogue.py`
- **ReadinessContext** (15 connections) — `agent-verse-backend/app/rag/catalogue.py`
- **UnavailableRAGStrategyError** (15 connections) — `agent-verse-backend/app/rag/contracts.py`
- **rag/certification.py** (14 connections) — `agent-verse-backend/app/rag/certification.py`
- **ResolvedLLM** (13 connections) — `agent-verse-backend/app/rag/gateway.py`
- **ReadinessFact** (12 connections) — `agent-verse-backend/app/rag/catalogue.py`
- **RAGCitation** (12 connections) — `agent-verse-backend/app/rag/contracts.py`
- **MinimalCitationVerifier (entailment-based)** (11 connections) — `agent-verse-backend/app/rag_platform/retriever.py`
- **.synthesize()** (11 connections) — `agent-verse-backend/app/rag_platform/retriever.py`
- **RAGAdapterConfiguration** (10 connections) — `agent-verse-backend/app/rag/catalogue.py`
- **core_strategy_capabilities()** (10 connections) — `agent-verse-backend/app/rag/gateway.py`
- **._resolve_llm()** (10 connections) — `agent-verse-backend/app/rag/gateway.py`
- **probe_colbert_readiness()** (10 connections) — `agent-verse-backend/app/rag/readiness.py`
- **RAGCapabilityCatalogueEntry** (9 connections) — `agent-verse-backend/app/rag/catalogue.py`
- **.readiness()** (9 connections) — `agent-verse-backend/app/rag/gateway.py`
- **.retrieve()** (9 connections) — `agent-verse-backend/app/rag_platform/retriever.py`
- **RAGSynthesisError** (9 connections) — `agent-verse-backend/app/rag_platform/retriever.py`
- *... and 109 more nodes in this community*

## Relationships

- [Federated RAG Search](Federated_RAG_Search.md) (66 shared connections)
- [Knowledge Ingestion API](Knowledge_Ingestion_API.md) (20 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (15 shared connections)
- [Agent Pattern Adapters (AutoGPT/BabyAGI/CodeAct)](Agent_Pattern_Adapters_AutoGPT-BabyAGI-CodeAct.md) (11 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (10 shared connections)
- [Community 76](Community_76.md) (10 shared connections)
- [Community 194](Community_194.md) (9 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (7 shared connections)
- [Community 159](Community_159.md) (6 shared connections)
- [Adaptive RAG Pattern](Adaptive_RAG_Pattern.md) (5 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (5 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (4 shared connections)

## Source Files

- `agent-verse-backend/app/rag/__init__.py`
- `agent-verse-backend/app/rag/catalogue.py`
- `agent-verse-backend/app/rag/certification.py`
- `agent-verse-backend/app/rag/contracts.py`
- `agent-verse-backend/app/rag/gateway.py`
- `agent-verse-backend/app/rag/readiness.py`
- `agent-verse-backend/app/rag_platform/retriever.py`

## Audit Trail

- EXTRACTED: 450 (96%)
- INFERRED: 18 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*