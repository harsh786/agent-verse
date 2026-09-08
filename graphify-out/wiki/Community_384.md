# Community 384

> 17 nodes · cohesion 0.14

## Key Concepts

- **tenancy/rbac.py** (13 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **effective_roles (role-hierarchy expansion)** (6 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **has_any_role()** (4 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **has_role()** (4 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **load_roles_from_db()** (4 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **require_role (FastAPI RBAC dependency)** (4 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **extract_roles_from_jwt()** (3 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **is_ip_allowed (CIDR allowlist)** (2 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **Any** (2 connections)
- **Role-Based Access Control helpers for AgentVerse. Roles (most privileged…** (1 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **Extract AgentVerse roles from a Keycloak JWT payload. Looks for roles in: 1.…** (1 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **Return True if client_ip is allowed by the allowlist. Empty allowlist = no…** (1 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **Expand ctx.roles with implied roles from hierarchy.** (1 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **Return True if ctx has the given role (with hierarchy expansion).** (1 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **Return True if ctx has any of the given roles.** (1 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **FastAPI dependency factory that enforces role requirement. Usage:…** (1 connections) — `agent-verse-backend/app/tenancy/rbac.py`
- **Load roles for a user from the user_roles table. Returns empty tuple if DB…** (1 connections) — `agent-verse-backend/app/tenancy/rbac.py`

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (4 shared connections)
- [Tenant API Keys/IP Allowlist](Tenant_API_Keys-IP_Allowlist.md) (3 shared connections)
- [Community 72](Community_72.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 227](Community_227.md) (1 shared connections)
- [Community 49](Community_49.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/tenancy/rbac.py`

## Audit Trail

- EXTRACTED: 28 (90%)
- INFERRED: 2 (6%)
- AMBIGUOUS: 1 (3%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*