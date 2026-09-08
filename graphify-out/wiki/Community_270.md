# Community 270

> 24 nodes · cohesion 0.17

## Key Concepts

- **SQLRAFTRepository** (23 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **._tenant_session()** (14 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **_job_record()** (10 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **.select()** (9 connections) — `agent-verse-backend/app/rag/agentic/retrieval_policy.py`
- **_require_matching_tenant()** (9 connections) — `agent-verse-backend/app/rag/raft.py`
- **.transition_job()** (8 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **.list_completed_models()** (6 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **.consume_confirmation()** (5 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **.find_completed_job()** (5 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **.get_dataset()** (5 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **.get_job()** (5 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **.save_confirmation()** (5 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **.save_dataset()** (5 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **.save_job()** (5 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **retrieval_policy.py** (4 connections) — `agent-verse-backend/app/rag/agentic/retrieval_policy.py`
- **RetrievalStrategy** (3 connections) — `agent-verse-backend/app/rag/agentic/retrieval_policy.py`
- **.find_completed_model()** (3 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **.load_chunks()** (3 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **RetrievalPolicy** (2 connections) — `agent-verse-backend/app/rag/agentic/retrieval_policy.py`
- **AsyncSession** (2 connections)
- **.__init__()** (2 connections) — `agent-verse-backend/app/rag/raft_repository.py`
- **StrEnum** (1 connections)
- **RetrievalPolicy — selects retrieval strategy based on query + source…** (1 connections) — `agent-verse-backend/app/rag/agentic/retrieval_policy.py`
- **Persist all RAFT state inside tenant RLS transactions.** (1 connections) — `agent-verse-backend/app/rag/raft_repository.py`

## Relationships

- [Community 375](Community_375.md) (15 shared connections)
- [Community 257](Community_257.md) (12 shared connections)
- [Community 256](Community_256.md) (4 shared connections)
- [Community 303](Community_303.md) (4 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Community 178](Community_178.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [BM25 Retrieval](BM25_Retrieval.md) (1 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/rag/agentic/retrieval_policy.py`
- `agent-verse-backend/app/rag/raft.py`
- `agent-verse-backend/app/rag/raft_repository.py`

## Audit Trail

- EXTRACTED: 78 (88%)
- INFERRED: 11 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*