# Community 488

> 12 nodes · cohesion 0.27

## Key Concepts

- **Any** (9 connections)
- **PolicyVersionManager (immutable policy snapshots)** (8 connections) — `agent-verse-backend/app/governance/policies.py`
- **._insert_version()** (6 connections) — `agent-verse-backend/app/governance/policies.py`
- **.create_policy()** (4 connections) — `agent-verse-backend/app/governance/policies.py`
- **.rollback()** (4 connections) — `agent-verse-backend/app/governance/policies.py`
- **.update_policy()** (4 connections) — `agent-verse-backend/app/governance/policies.py`
- **.get_version_history()** (3 connections) — `agent-verse-backend/app/governance/policies.py`
- **Manages the version lifecycle for policies stored in policy_versions. Every…** (1 connections) — `agent-verse-backend/app/governance/policies.py`
- **Persist a brand-new policy at version 1.** (1 connections) — `agent-verse-backend/app/governance/policies.py`
- **Atomically deactivate the current version and create the next one.** (1 connections) — `agent-verse-backend/app/governance/policies.py`
- **Create a new version that is a copy of a historical snapshot.** (1 connections) — `agent-verse-backend/app/governance/policies.py`
- **Return all version snapshots for a policy, oldest first.** (1 connections) — `agent-verse-backend/app/governance/policies.py`

## Relationships

- [Community 447](Community_447.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 286](Community_286.md) (1 shared connections)
- [Community 197](Community_197.md) (1 shared connections)
- [Community 72](Community_72.md) (1 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/governance/policies.py`

## Audit Trail

- EXTRACTED: 24 (96%)
- INFERRED: 1 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*