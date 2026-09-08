# Community 81

> 51 nodes · cohesion 0.07

## Key Concepts

- **TenantService** (24 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **tenant_service.py** (18 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **Tenant** (13 connections) — `agent-verse-backend/app/db/models/tenant.py`
- **Any** (11 connections)
- **.create_api_key()** (9 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **.create_tenant()** (8 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **ApiKey** (7 connections) — `agent-verse-backend/app/db/models/tenant.py`
- **_hash_key()** (6 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **._db_create_api_key()** (6 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **._db_create_tenant_with_api_key()** (6 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **.get_tenant_cached()** (6 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **._get_tenant_from_db()** (6 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **.invalidate_tenant_cache()** (6 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **tenant.py** (5 connections) — `agent-verse-backend/app/db/models/tenant.py`
- **.resolve_api_key()** (5 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **.revoke_api_key()** (5 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **_generate_raw_key()** (4 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **.create_tenant_from_sso()** (4 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **._db_create_tenant()** (4 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **._db_revoke_api_key()** (4 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **.get_tenant()** (4 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **datetime** (3 connections)
- **.get_key_by_sso_sub()** (3 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **.get_tenant_by_sso_sub()** (3 connections) — `agent-verse-backend/app/services/tenant_service.py`
- **.list_api_keys()** (3 connections) — `agent-verse-backend/app/services/tenant_service.py`
- *... and 26 more nodes in this community*

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (10 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (6 shared connections)
- [Chat DB Models](Chat_DB_Models.md) (4 shared connections)
- [Community 57](Community_57.md) (4 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (3 shared connections)
- [Community 155](Community_155.md) (3 shared connections)
- [Agent Store API](Agent_Store_API.md) (2 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Community 248](Community_248.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/db/models/tenant.py`
- `agent-verse-backend/app/services/tenant_service.py`

## Audit Trail

- EXTRACTED: 115 (96%)
- INFERRED: 5 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*