# Memory-driven Improvement

> 76 nodes · cohesion 0.05

## Key Concepts

- **MemoryRecord** (19 connections) — `agent-verse-backend/app/memory/contracts.py`
- **postgres_repository.py** (18 connections) — `agent-verse-backend/app/memory/postgres_repository.py`
- **memory/contracts.py** (14 connections) — `agent-verse-backend/app/memory/contracts.py`
- **memory/repository.py** (14 connections) — `agent-verse-backend/app/memory/repository.py`
- **MemoryWriteRequest** (12 connections) — `agent-verse-backend/app/memory/contracts.py`
- **backfill_memory_rows (idempotent compat backfill)** (11 connections) — `agent-verse-backend/app/memory/backfill.py`
- **MemoryFeedback** (10 connections) — `agent-verse-backend/app/memory/contracts.py`
- **LearningExperimentService** (9 connections) — `agent-verse-backend/app/intelligence/learning_experiments.py`
- **backfill.py** (9 connections) — `agent-verse-backend/app/memory/backfill.py`
- **MemoryRecallRequest** (9 connections) — `agent-verse-backend/app/memory/contracts.py`
- **PostgresMemoryRepository** (8 connections) — `agent-verse-backend/app/memory/postgres_repository.py`
- **ReflexionService** (8 connections) — `agent-verse-backend/app/memory/reflexion.py`
- **InMemoryMemoryRepository** (8 connections) — `agent-verse-backend/app/memory/repository.py`
- **MemoryRepository** (8 connections) — `agent-verse-backend/app/memory/repository.py`
- **MemoryRecallHit** (7 connections) — `agent-verse-backend/app/memory/contracts.py`
- **BaseModel** (7 connections)
- **_record()** (7 connections) — `agent-verse-backend/app/memory/postgres_repository.py`
- **memory/reflexion.py** (7 connections) — `agent-verse-backend/app/memory/reflexion.py`
- **_similarity (cosine w/ token-overlap fallback)** (7 connections) — `agent-verse-backend/app/memory/repository.py`
- **ExperimentSpec** (6 connections) — `agent-verse-backend/app/memory/contracts.py`
- **.recall()** (6 connections) — `agent-verse-backend/app/memory/postgres_repository.py`
- **ImprovementActionExecutor** (5 connections) — `agent-verse-backend/app/intelligence/improvement_action_executor.py`
- **learning_experiments.py** (5 connections) — `agent-verse-backend/app/intelligence/learning_experiments.py`
- **CheckpointWriter** (5 connections) — `agent-verse-backend/app/memory/backfill.py`
- **.feedback()** (5 connections) — `agent-verse-backend/app/memory/postgres_repository.py`
- *... and 51 more nodes in this community*

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (10 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (6 shared connections)
- [Group Chat Coordination](Group_Chat_Coordination.md) (4 shared connections)
- [Community 68](Community_68.md) (4 shared connections)
- [Chat DB Models](Chat_DB_Models.md) (4 shared connections)
- [Community 283](Community_283.md) (3 shared connections)
- [Coordination Contracts](Coordination_Contracts.md) (2 shared connections)
- [Community 175](Community_175.md) (1 shared connections)
- [Community 150](Community_150.md) (1 shared connections)
- [Community 619](Community_619.md) (1 shared connections)
- [Community 358](Community_358.md) (1 shared connections)
- [Community 217](Community_217.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/intelligence/improvement_action_executor.py`
- `agent-verse-backend/app/intelligence/learning_experiments.py`
- `agent-verse-backend/app/memory/backfill.py`
- `agent-verse-backend/app/memory/contracts.py`
- `agent-verse-backend/app/memory/postgres_repository.py`
- `agent-verse-backend/app/memory/reflexion.py`
- `agent-verse-backend/app/memory/repository.py`

## Audit Trail

- EXTRACTED: 179 (96%)
- INFERRED: 7 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*