# Community 272

> 24 nodes · cohesion 0.11

## Key Concepts

- **SubTenantService (enterprise sub-tenant hierarchy)** (10 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **sub_tenants.py** (8 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **SubTenant** (5 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **.get_hierarchy()** (5 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **.list_by_parent()** (4 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **SubTenantModel** (3 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **.create()** (3 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **TenantHierarchy** (3 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **.get()** (2 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **.update_budget()** (2 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **TenantHierarchyNode** (2 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **Any** (1 connections)
- **Base** (1 connections)
- **QA4 — Sub-Tenants (Enterprise Hierarchy). Enterprise orgs need hierarchy: Acme…** (1 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **QA4 — Sub-tenant management service. Production: backed by `sub_tenants` DB…** (1 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **POST /v1/tenants/{id}/sub-tenants** (1 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **GET /v1/tenants/{id}/sub-tenants** (1 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **PATCH /v1/sub-tenants/{id}/budget** (1 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **GET /v1/tenants/{id}/hierarchy — full tree view** (1 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **DB model for sub-tenants.** (1 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **Sub-tenant entity per spec QA4.** (1 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **Full tenant hierarchy tree.** (1 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`
- **.deactivate()** (1 connections) — `agent-verse-backend/app/tenancy/sub_tenants.py`

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/tenancy/sub_tenants.py`

## Audit Trail

- EXTRACTED: 31 (97%)
- INFERRED: 1 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*