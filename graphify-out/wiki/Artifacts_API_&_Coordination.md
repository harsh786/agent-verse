# Artifacts API & Coordination

> 87 nodes · cohesion 0.04

## Key Concepts

- **sqlalchemy_rls_context()** (200 connections) — `agent-verse-backend/app/db/rls.py`
- **rls.py** (24 connections) — `agent-verse-backend/app/db/rls.py`
- **models/coordination.py** (15 connections) — `agent-verse-backend/app/db/models/coordination.py`
- **auction/repository.py** (13 connections) — `agent-verse-backend/app/coordination/auction/repository.py`
- **state_repository.py** (13 connections) — `agent-verse-backend/app/coordination/state_repository.py`
- **membership.py** (11 connections) — `agent-verse-backend/app/coordination/handoffs/membership.py`
- **vector_cache_backend.py** (10 connections) — `agent-verse-backend/app/rag/vector_cache_backend.py`
- **PgVectorCacheBackend** (9 connections) — `agent-verse-backend/app/rag/vector_cache_backend.py`
- **Any** (9 connections)
- **api/artifacts.py** (8 connections) — `agent-verse-backend/app/api/artifacts.py`
- **get_artifact()** (8 connections) — `agent-verse-backend/app/api/artifacts.py`
- **list_artifacts()** (8 connections) — `agent-verse-backend/app/api/artifacts.py`
- **InMemoryCacheBackend** (8 connections) — `agent-verse-backend/app/rag/vector_cache_backend.py`
- **PostgresSealedBidInbox** (7 connections) — `agent-verse-backend/app/coordination/auction/repository.py`
- **DatabaseHandoffMembership** (7 connections) — `agent-verse-backend/app/coordination/handoffs/membership.py`
- **CacheBackend** (7 connections) — `agent-verse-backend/app/rag/vector_cache_backend.py`
- **_get_rls_imports()** (7 connections) — `agent-verse-backend/app/rag/vector_cache_backend.py`
- **delete_artifact()** (6 connections) — `agent-verse-backend/app/api/artifacts.py`
- **_require_tenant()** (6 connections) — `agent-verse-backend/app/api/artifacts.py`
- **InMemorySealedBidInbox** (6 connections) — `agent-verse-backend/app/coordination/auction/repository.py`
- **Artifact** (6 connections) — `agent-verse-backend/app/db/models/artifacts.py`
- **SealedBidReceipt** (5 connections) — `agent-verse-backend/app/coordination/auction/repository.py`
- **DatabaseSessionAuthorizer** (5 connections) — `agent-verse-backend/app/coordination/handoffs/membership.py`
- **InMemorySessionAuthorizer** (5 connections) — `agent-verse-backend/app/coordination/handoffs/membership.py`
- **Request** (4 connections)
- *... and 62 more nodes in this community*

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (26 shared connections)
- [BM25 Retrieval](BM25_Retrieval.md) (17 shared connections)
- [Agent Store API](Agent_Store_API.md) (10 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (9 shared connections)
- [Coordination Contracts](Coordination_Contracts.md) (8 shared connections)
- [Community 312](Community_312.md) (8 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (8 shared connections)
- [Community 68](Community_68.md) (7 shared connections)
- [Community 71](Community_71.md) (7 shared connections)
- [Tenant API Keys/IP Allowlist](Tenant_API_Keys-IP_Allowlist.md) (7 shared connections)
- [Chat DB Models](Chat_DB_Models.md) (6 shared connections)
- [Community 69](Community_69.md) (6 shared connections)

## Source Files

- `agent-verse-backend/app/api/artifacts.py`
- `agent-verse-backend/app/coordination/auction/repository.py`
- `agent-verse-backend/app/coordination/handoffs/membership.py`
- `agent-verse-backend/app/coordination/state_repository.py`
- `agent-verse-backend/app/db/models/artifacts.py`
- `agent-verse-backend/app/db/models/coordination.py`
- `agent-verse-backend/app/db/rls.py`
- `agent-verse-backend/app/rag/vector_cache_backend.py`

## Audit Trail

- EXTRACTED: 375 (99%)
- INFERRED: 5 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*