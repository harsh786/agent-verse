# Community 283

> 23 nodes · cohesion 0.11

## Key Concepts

- **MemoryRecord (canonical evidence-backed memory)** (9 connections) — `agent-verse-backend/app/memory/contracts.py`
- **PostgresMemoryRepository (RLS-scoped canonical store)** (7 connections) — `agent-verse-backend/app/memory/postgres_repository.py`
- **MemoryKind (7 memory kinds enum)** (5 connections) — `agent-verse-backend/app/memory/contracts.py`
- **ReflexionService (lesson learn/recall/effectiveness)** (5 connections) — `agent-verse-backend/app/memory/reflexion.py`
- **MemoryConsolidator v2 (dedup + stale/archive lifecycle)** (5 connections) — `agent-verse-backend/app/memory_v2/consolidation.py`
- **ImprovementActionRecord (self-improvement action)** (4 connections) — `agent-verse-backend/app/memory/contracts.py`
- **LifecycleState (active/quarantined/disputed/expired/deleted)** (4 connections) — `agent-verse-backend/app/memory/contracts.py`
- **MemoryRepository (canonical async protocol)** (4 connections) — `agent-verse-backend/app/memory/repository.py`
- **Classification (public/internal/confidential/restricted)** (3 connections) — `agent-verse-backend/app/memory/contracts.py`
- **prompt-injection quarantine on write (poison markers / missing evidence)** (3 connections) — `agent-verse-backend/app/memory/repository.py`
- **MemoryLifecycleState v2 (active/stale/disputed/archived/deleted)** (3 connections) — `agent-verse-backend/app/memory_v2/models.py`
- **PromptVariantSelector (deterministic per-goal A/B)** (2 connections) — `agent-verse-backend/app/context/prompt_variant_selector.py`
- **MemoryFeedback (helpful/harmful outcome signal)** (2 connections) — `agent-verse-backend/app/memory/contracts.py`
- **MemoryRecallRequest (scoped, budgeted recall)** (2 connections) — `agent-verse-backend/app/memory/contracts.py`
- **InMemoryMemoryRepository (reference adapter)** (2 connections) — `agent-verse-backend/app/memory/repository.py`
- **MemoryConflict (contradicting memory pair)** (2 connections) — `agent-verse-backend/app/memory_v2/models.py`
- **embedding_matches_profile validator (memory-embedding-v1/1536)** (1 connections) — `agent-verse-backend/app/memory/contracts.py`
- **ExperimentSpec (prompt/model/rag A-B experiment)** (1 connections) — `agent-verse-backend/app/memory/contracts.py`
- **_record (row → MemoryRecord mapper)** (1 connections) — `agent-verse-backend/app/memory/postgres_repository.py`
- **sensitive-class redaction (encrypted content_ref + [REDACTED] summary)** (1 connections) — `agent-verse-backend/app/memory/postgres_repository.py`
- **retention cutoffs (30d stale, 90d archive)** (1 connections) — `agent-verse-backend/app/memory_v2/consolidation.py`
- **MemoryPrivacyClass (public/internal/confidential/pii/phi)** (1 connections) — `agent-verse-backend/app/memory_v2/models.py`
- **MemoryProvenance (goal/tool/document origin)** (1 connections) — `agent-verse-backend/app/memory_v2/models.py`

## Relationships

- [Community 217](Community_217.md) (5 shared connections)
- [Memory-driven Improvement](Memory-driven_Improvement.md) (3 shared connections)
- [Community 467](Community_467.md) (1 shared connections)
- [Community 278](Community_278.md) (1 shared connections)
- [Community 430](Community_430.md) (1 shared connections)
- [Community 464](Community_464.md) (1 shared connections)
- [Community 341](Community_341.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/context/prompt_variant_selector.py`
- `agent-verse-backend/app/memory/contracts.py`
- `agent-verse-backend/app/memory/postgres_repository.py`
- `agent-verse-backend/app/memory/reflexion.py`
- `agent-verse-backend/app/memory/repository.py`
- `agent-verse-backend/app/memory_v2/consolidation.py`
- `agent-verse-backend/app/memory_v2/models.py`

## Audit Trail

- EXTRACTED: 21 (51%)
- INFERRED: 19 (46%)
- AMBIGUOUS: 1 (2%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*