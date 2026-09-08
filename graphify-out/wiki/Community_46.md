# Community 46

> 73 nodes · cohesion 0.04

## Key Concepts

- **scope_enforcement.py** (17 connections) — `agent-verse-backend/app/auth/scope_enforcement.py`
- **ScopeEnforcementMiddleware** (17 connections) — `agent-verse-backend/app/auth/scope_enforcement.py`
- **PermissionCache** (13 connections) — `agent-verse-backend/app/auth/permission_cache.py`
- **IPAllowlistCache** (10 connections) — `agent-verse-backend/app/auth/ip_allowlist.py`
- **.dispatch()** (9 connections) — `agent-verse-backend/app/auth/scope_enforcement.py`
- **models/auth.py** (9 connections) — `agent-verse-backend/app/db/models/auth.py`
- **warm_permission_cache()** (8 connections) — `agent-verse-backend/app/auth/cache_warmer.py`
- **cache_warmer.py** (6 connections) — `agent-verse-backend/app/auth/cache_warmer.py`
- **CustomRole** (6 connections) — `agent-verse-backend/app/db/models/auth.py`
- **Base** (6 connections)
- **ip_allowlist.py** (5 connections) — `agent-verse-backend/app/auth/ip_allowlist.py`
- **is_ip_allowed()** (5 connections) — `agent-verse-backend/app/auth/ip_allowlist.py`
- **_get_client_ip()** (5 connections) — `agent-verse-backend/app/auth/scope_enforcement.py`
- **._load_scopes()** (5 connections) — `agent-verse-backend/app/auth/scope_enforcement.py`
- **APIKeyScope** (5 connections) — `agent-verse-backend/app/db/models/auth.py`
- **IPAllowlistEntry** (5 connections) — `agent-verse-backend/app/db/models/auth.py`
- **RoleAssignment** (5 connections) — `agent-verse-backend/app/db/models/auth.py`
- **.get_cidrs()** (4 connections) — `agent-verse-backend/app/auth/ip_allowlist.py`
- **._key()** (4 connections) — `agent-verse-backend/app/auth/permission_cache.py`
- **RoleResolver** (4 connections) — `agent-verse-backend/app/auth/scope_enforcement.py`
- **._client_ip()** (4 connections) — `agent-verse-backend/app/auth/scope_enforcement.py`
- **.invalidate()** (3 connections) — `agent-verse-backend/app/auth/ip_allowlist.py`
- **._key()** (3 connections) — `agent-verse-backend/app/auth/ip_allowlist.py`
- **permission_cache.py** (3 connections) — `agent-verse-backend/app/auth/permission_cache.py`
- **.get()** (3 connections) — `agent-verse-backend/app/auth/permission_cache.py`
- *... and 48 more nodes in this community*

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (5 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (2 shared connections)
- [Chat DB Models](Chat_DB_Models.md) (2 shared connections)
- [Community 136](Community_136.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/auth/cache_warmer.py`
- `agent-verse-backend/app/auth/ip_allowlist.py`
- `agent-verse-backend/app/auth/permission_cache.py`
- `agent-verse-backend/app/auth/scope_enforcement.py`
- `agent-verse-backend/app/db/models/auth.py`

## Audit Trail

- EXTRACTED: 115 (93%)
- INFERRED: 9 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*