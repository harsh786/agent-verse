# Community 321

> 20 nodes · cohesion 0.17

## Key Concepts

- **skills_runtime/executor.py** (13 connections) — `agent-verse-backend/app/skills_runtime/executor.py`
- **_dict_to_skill_def()** (9 connections) — `agent-verse-backend/app/api/skills_runtime.py`
- **execute_best_match()** (9 connections) — `agent-verse-backend/app/api/skills_runtime.py`
- **SkillDefinition** (9 connections) — `agent-verse-backend/app/skills_runtime/models.py`
- **SkillExecutor** (8 connections) — `agent-verse-backend/app/skills_runtime/executor.py`
- **skills_runtime/models.py** (8 connections) — `agent-verse-backend/app/skills_runtime/models.py`
- **.execute()** (7 connections) — `agent-verse-backend/app/skills_runtime/executor.py`
- **.execute_best_match()** (5 connections) — `agent-verse-backend/app/skills_runtime/executor.py`
- **SkillExecution** (5 connections) — `agent-verse-backend/app/skills_runtime/models.py`
- **SkillScope** (5 connections) — `agent-verse-backend/app/skills_runtime/models.py`
- **SkillStatus** (5 connections) — `agent-verse-backend/app/skills_runtime/models.py`
- **StrEnum** (2 connections)
- **Auto-match the best skill for a goal and execute it.** (1 connections) — `agent-verse-backend/app/api/skills_runtime.py`
- **Convert an in-memory skill dict to a SkillDefinition dataclass.** (1 connections) — `agent-verse-backend/app/api/skills_runtime.py`
- **Skills Runtime execution engine. Responsibilities: 1. TriggerMatcher: score how…** (1 connections) — `agent-verse-backend/app/skills_runtime/executor.py`
- **Execute a skill against an LLM provider.** (1 connections) — `agent-verse-backend/app/skills_runtime/executor.py`
- **Execute a skill. Steps: 1. Check permission via ScopedPermissionChecker → raise…** (1 connections) — `agent-verse-backend/app/skills_runtime/executor.py`
- **Find best matching skill and execute it. Returns None if no match.** (1 connections) — `agent-verse-backend/app/skills_runtime/executor.py`
- **Skills Runtime data models.** (1 connections) — `agent-verse-backend/app/skills_runtime/models.py`
- **A skill execution trace.** (1 connections) — `agent-verse-backend/app/skills_runtime/models.py`

## Relationships

- [Community 418](Community_418.md) (11 shared connections)
- [Community 204](Community_204.md) (5 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (5 shared connections)
- [Community 553](Community_553.md) (4 shared connections)
- [Community 518](Community_518.md) (2 shared connections)
- [Community 82](Community_82.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/api/skills_runtime.py`
- `agent-verse-backend/app/skills_runtime/executor.py`
- `agent-verse-backend/app/skills_runtime/models.py`

## Audit Trail

- EXTRACTED: 61 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*