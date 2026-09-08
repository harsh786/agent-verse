# Community 278

> 23 nodes · cohesion 0.12

## Key Concepts

- **ProcedureContract** (9 connections) — `agent-verse-backend/app/memory/procedural_validator.py`
- **validate_procedure (tenant/policy/capability/schema gate)** (7 connections) — `agent-verse-backend/app/memory/procedural_validator.py`
- **.execute()** (6 connections) — `agent-verse-backend/app/agent/patterns/voyager.py`
- **VoyagerRuntime** (5 connections) — `agent-verse-backend/app/agent/patterns/voyager.py`
- **procedural_validator.py** (5 connections) — `agent-verse-backend/app/memory/procedural_validator.py`
- **voyager_skills.py** (5 connections) — `agent-verse-backend/app/memory/voyager_skills.py`
- **VoyagerSkillStore** (4 connections) — `agent-verse-backend/app/memory/voyager_skills.py`
- **Any** (3 connections)
- **.create_runtime()** (3 connections) — `agent-verse-backend/app/agent/patterns/voyager.py`
- **VoyagerState** (3 connections) — `agent-verse-backend/app/agent/patterns/voyager.py`
- **ProcedureContract (versioned skill contract)** (3 connections) — `agent-verse-backend/app/memory/procedural_validator.py`
- **ProspectiveMemory (intention + due_at + fencing token)** (3 connections) — `agent-verse-backend/app/memory/prospective.py`
- **VoyagerSkillStore (immutable versioned skill publication)** (3 connections) — `agent-verse-backend/app/memory/voyager_skills.py`
- **.publish()** (3 connections) — `agent-verse-backend/app/memory/voyager_skills.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/agent/patterns/voyager.py`
- **BaseModel** (1 connections)
- **Event** (1 connections)
- **BaseModel** (1 connections)
- **Versioned procedural-memory validation before every reuse.** (1 connections) — `agent-verse-backend/app/memory/procedural_validator.py`
- **lease_due (fencing-token lease of due intentions)** (1 connections) — `agent-verse-backend/app/memory/prospective.py`
- **ProspectiveMemoryService (leased future intentions)** (1 connections) — `agent-verse-backend/app/memory/prospective.py`
- **Governed versioned Voyager skill publication.** (1 connections) — `agent-verse-backend/app/memory/voyager_skills.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/memory/voyager_skills.py`

## Relationships

- [Agent Pattern Adapters (AutoGPT/BabyAGI/CodeAct)](Agent_Pattern_Adapters_AutoGPT-BabyAGI-CodeAct.md) (5 shared connections)
- [Community 217](Community_217.md) (2 shared connections)
- [Community 94](Community_94.md) (1 shared connections)
- [Community 467](Community_467.md) (1 shared connections)
- [Community 283](Community_283.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/patterns/voyager.py`
- `agent-verse-backend/app/memory/procedural_validator.py`
- `agent-verse-backend/app/memory/prospective.py`
- `agent-verse-backend/app/memory/voyager_skills.py`

## Audit Trail

- EXTRACTED: 34 (83%)
- INFERRED: 6 (15%)
- AMBIGUOUS: 1 (2%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*