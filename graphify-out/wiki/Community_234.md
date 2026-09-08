# Community 234

> 26 nodes · cohesion 0.13

## Key Concepts

- **LongTermMemoryStore** (20 connections) — `agent-verse-backend/app/memory/long_term.py`
- **LongTermMemory** (13 connections) — `agent-verse-backend/app/memory/long_term.py`
- **long_term.py** (10 connections) — `agent-verse-backend/app/memory/long_term.py`
- **.store_async()** (9 connections) — `agent-verse-backend/app/memory/long_term.py`
- **.recall_async()** (8 connections) — `agent-verse-backend/app/memory/long_term.py`
- **.extract_from_goal_async()** (6 connections) — `agent-verse-backend/app/memory/long_term.py`
- **.store_rpa_extraction()** (6 connections) — `agent-verse-backend/app/memory/long_term.py`
- **.process_feedback_batch()** (5 connections) — `agent-verse-backend/app/evals/self_improvement_engine.py`
- **.extract_from_goal()** (5 connections) — `agent-verse-backend/app/memory/long_term.py`
- **.recall()** (5 connections) — `agent-verse-backend/app/memory/long_term.py`
- **.store()** (5 connections) — `agent-verse-backend/app/memory/long_term.py`
- **Any** (4 connections)
- **.list_all()** (3 connections) — `agent-verse-backend/app/memory/long_term.py`
- **.delete()** (2 connections) — `agent-verse-backend/app/memory/long_term.py`
- **Any** (1 connections)
- **Read unprocessed rows from ``goal_feedback`` and derive improvement actions.…** (1 connections) — `agent-verse-backend/app/evals/self_improvement_engine.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/memory/long_term.py`
- **Long-term memory — cross-session learnings persisted across agent runs. Stores…** (1 connections) — `agent-verse-backend/app/memory/long_term.py`
- **Extract a learning from a completed goal and persist it. Adds to the in-memory…** (1 connections) — `agent-verse-backend/app/memory/long_term.py`
- **Async store — persists to DB with embedding if embedder available. Computes a…** (1 connections) — `agent-verse-backend/app/memory/long_term.py`
- **A single cross-session learning entry.** (1 connections) — `agent-verse-backend/app/memory/long_term.py`
- **Store RPA-extracted page content into LTM. Short content (<50 chars) is ignored…** (1 connections) — `agent-verse-backend/app/memory/long_term.py`
- **Recall memories using pgvector cosine similarity when embedder available. Falls…** (1 connections) — `agent-verse-backend/app/memory/long_term.py`
- **Per-tenant store for cross-session learnings. Learnings are extracted from…** (1 connections) — `agent-verse-backend/app/memory/long_term.py`
- **Recall relevant memories using simple keyword matching.** (1 connections) — `agent-verse-backend/app/memory/long_term.py`
- *... and 1 more nodes in this community*

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (8 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (7 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (3 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (3 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (3 shared connections)
- [Community 373](Community_373.md) (2 shared connections)
- [Eval Scoring](Eval_Scoring.md) (1 shared connections)
- [Community 154](Community_154.md) (1 shared connections)
- [Reliability & Audit](Reliability_&_Audit.md) (1 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (1 shared connections)
- [Community 102](Community_102.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/evals/self_improvement_engine.py`
- `agent-verse-backend/app/memory/long_term.py`

## Audit Trail

- EXTRACTED: 68 (94%)
- INFERRED: 4 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*