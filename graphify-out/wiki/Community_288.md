# Community 288

> 23 nodes · cohesion 0.13

## Key Concepts

- **ProspectiveMemoryService** (11 connections) — `agent-verse-backend/app/memory/prospective.py`
- **ProspectiveMemory** (9 connections) — `agent-verse-backend/app/memory/prospective.py`
- **process_due_memories()** (8 connections) — `agent-verse-backend/app/scaling/memory_tasks.py`
- **prospective.py** (6 connections) — `agent-verse-backend/app/memory/prospective.py`
- **memory_tasks.py** (6 connections) — `agent-verse-backend/app/scaling/memory_tasks.py`
- **.lease_due()** (4 connections) — `agent-verse-backend/app/memory/prospective.py`
- **.complete()** (3 connections) — `agent-verse-backend/app/memory/prospective.py`
- **.cancel()** (2 connections) — `agent-verse-backend/app/memory/prospective.py`
- **.create()** (2 connections) — `agent-verse-backend/app/memory/prospective.py`
- **.get()** (2 connections) — `agent-verse-backend/app/memory/prospective.py`
- **datetime** (2 connections)
- **datetime** (2 connections)
- **prospective_id()** (1 connections) — `agent-verse-backend/app/memory/prospective.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/memory/prospective.py`
- **Any** (1 connections)
- **BaseModel** (1 connections)
- **timedelta** (1 connections)
- **Idempotent leased prospective-memory lifecycle.** (1 connections) — `agent-verse-backend/app/memory/prospective.py`
- **timedelta** (1 connections)
- **Bounded prospective-memory execution used by Celery maintenance workers.** (1 connections) — `agent-verse-backend/app/scaling/memory_tasks.py`
- **Claim and complete a bounded batch; duplicate workers lose fencing races.** (1 connections) — `agent-verse-backend/app/scaling/memory_tasks.py`
- **PolicyCheck** (1 connections)
- **ProspectiveHandler** (1 connections)

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/memory/prospective.py`
- `agent-verse-backend/app/scaling/memory_tasks.py`

## Audit Trail

- EXTRACTED: 33 (94%)
- INFERRED: 2 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*