# Community 378

> 17 nodes · cohesion 0.15

## Key Concepts

- **LegalHoldManager** (10 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **legal_holds.py** (5 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **.create_hold()** (4 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **.list_holds()** (3 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **.release_hold()** (3 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **.sync_cache()** (3 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **Any** (3 connections)
- **.__init__()** (2 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **.is_under_hold()** (2 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **datetime** (2 connections)
- **Legal hold lifecycle management for AgentVerse audit events. A legal hold…** (1 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **Release a hold and rebuild the cache from the remaining active holds.** (1 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **Return True if *resource_id* is under any active hold (O(1) via Redis).** (1 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **Return all holds of the given status for a tenant.** (1 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **Rebuild the Redis set from the DB for *tenant_id*.** (1 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **Full lifecycle for legal holds with Redis-cached membership checks.** (1 connections) — `agent-verse-backend/app/governance/legal_holds.py`
- **Persist a new legal hold and warm the Redis cache.** (1 connections) — `agent-verse-backend/app/governance/legal_holds.py`

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/governance/legal_holds.py`

## Audit Trail

- EXTRACTED: 24 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*