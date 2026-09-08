# Tenant API Keys/IP Allowlist

> 88 nodes · cohesion 0.04

## Key Concepts

- **tenants.py** (54 connections) — `agent-verse-backend/app/api/tenants.py`
- **Request** (27 connections)
- **_require_tenant()** (12 connections) — `agent-verse-backend/app/api/tenants.py`
- **_get_tenant_service()** (11 connections) — `agent-verse-backend/app/api/tenants.py`
- **get** (10 connections)
- **set_llm_config()** (9 connections) — `agent-verse-backend/app/api/tenants.py`
- **UserRole** (9 connections) — `agent-verse-backend/app/db/models/rbac.py`
- **create_ip_allowlist_entry()** (8 connections) — `agent-verse-backend/app/api/tenants.py`
- **create_key()** (8 connections) — `agent-verse-backend/app/api/tenants.py`
- **create_role()** (8 connections) — `agent-verse-backend/app/api/tenants.py`
- **BaseModel** (8 connections)
- **JSONResponse** (8 connections)
- **revoke_key()** (8 connections) — `agent-verse-backend/app/api/tenants.py`
- **rotate_key()** (8 connections) — `agent-verse-backend/app/api/tenants.py`
- **delete_ip_allowlist_entry()** (7 connections) — `agent-verse-backend/app/api/tenants.py`
- **delete_role()** (7 connections) — `agent-verse-backend/app/api/tenants.py`
- **get_me()** (7 connections) — `agent-verse-backend/app/api/tenants.py`
- **list_ip_allowlist()** (7 connections) — `agent-verse-backend/app/api/tenants.py`
- **list_keys()** (7 connections) — `agent-verse-backend/app/api/tenants.py`
- **list_roles()** (7 connections) — `agent-verse-backend/app/api/tenants.py`
- **signup()** (7 connections) — `agent-verse-backend/app/api/tenants.py`
- **IPAllowlistEntry** (7 connections) — `agent-verse-backend/app/db/models/rbac.py`
- **get_llm_config()** (6 connections) — `agent-verse-backend/app/api/tenants.py`
- **set_byok_vault_key()** (6 connections) — `agent-verse-backend/app/api/tenants.py`
- **delete_tenant()** (5 connections) — `agent-verse-backend/app/api/tenants.py`
- *... and 63 more nodes in this community*

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (17 shared connections)
- [Community 82](Community_82.md) (8 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (7 shared connections)
- [Community 155](Community_155.md) (3 shared connections)
- [Community 279](Community_279.md) (3 shared connections)
- [Community 384](Community_384.md) (3 shared connections)
- [Community 59](Community_59.md) (2 shared connections)
- [Community 102](Community_102.md) (2 shared connections)
- [Chat DB Models](Chat_DB_Models.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 248](Community_248.md) (1 shared connections)
- [Community 320](Community_320.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/api/tenants.py`
- `agent-verse-backend/app/db/models/rbac.py`

## Audit Trail

- EXTRACTED: 215 (97%)
- INFERRED: 7 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*