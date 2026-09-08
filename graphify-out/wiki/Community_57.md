# Community 57

> 65 nodes · cohesion 0.05

## Key Concepts

- **PlanTier / PLAN_LIMITS** (50 connections) — `agent-verse-backend/app/tenancy/context.py`
- **TenantScopedStore (Redis key-prefix tenant isolation)** (22 connections) — `agent-verse-backend/app/tenancy/store.py`
- **middleware.py** (18 connections) — `agent-verse-backend/app/tenancy/middleware.py`
- **mcp_server/__init__.py** (15 connections) — `agent-verse-backend/app/gateway/mcp_server/__init__.py`
- **tenancy/__init__.py** (13 connections) — `agent-verse-backend/app/tenancy/__init__.py`
- **TenantMiddleware (API-key auth + rate limit + MFA gate)** (12 connections) — `agent-verse-backend/app/tenancy/middleware.py`
- **.dispatch()** (12 connections) — `agent-verse-backend/app/tenancy/middleware.py`
- **SlidingWindowRateLimiter (Lua-atomic)** (11 connections) — `agent-verse-backend/app/tenancy/rate_limiter.py`
- **._key()** (11 connections) — `agent-verse-backend/app/tenancy/store.py`
- **SecurityHeadersMiddleware (OWASP headers)** (7 connections) — `agent-verse-backend/app/tenancy/middleware.py`
- **_try_resolve_sso()** (7 connections) — `agent-verse-backend/app/tenancy/middleware.py`
- **.eval()** (7 connections) — `agent-verse-backend/app/tenancy/store.py`
- **_check_rate_limit_with_fallback()** (6 connections) — `agent-verse-backend/app/tenancy/middleware.py`
- **tenancy/rate_limiter.py** (6 connections) — `agent-verse-backend/app/tenancy/rate_limiter.py`
- **RateLimiter (Redis or in-memory fallback)** (6 connections) — `agent-verse-backend/app/tenancy/rate_limiter.py`
- **Request** (5 connections)
- **.__init__()** (4 connections) — `agent-verse-backend/app/tenancy/middleware.py`
- **.check()** (4 connections) — `agent-verse-backend/app/tenancy/rate_limiter.py`
- **._get_lock()** (4 connections) — `agent-verse-backend/app/tenancy/rate_limiter.py`
- **tenancy/store.py** (4 connections) — `agent-verse-backend/app/tenancy/store.py`
- **_auth_error_response()** (3 connections) — `agent-verse-backend/app/tenancy/middleware.py`
- **_extract_key()** (3 connections) — `agent-verse-backend/app/tenancy/middleware.py`
- **_is_cors_preflight()** (3 connections) — `agent-verse-backend/app/tenancy/middleware.py`
- **JSONResponse** (3 connections)
- **_rate_limit_response()** (3 connections) — `agent-verse-backend/app/tenancy/middleware.py`
- *... and 40 more nodes in this community*

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (8 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (8 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (7 shared connections)
- [Community 106](Community_106.md) (6 shared connections)
- [Runtime Profile & Sandbox](Runtime_Profile_&_Sandbox.md) (5 shared connections)
- [Community 81](Community_81.md) (4 shared connections)
- [Community 247](Community_247.md) (2 shared connections)
- [Community 314](Community_314.md) (2 shared connections)
- [Community 74](Community_74.md) (2 shared connections)
- [Org Department Memory](Org_Department_Memory.md) (2 shared connections)
- [Community 295](Community_295.md) (2 shared connections)
- [Community 136](Community_136.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/gateway/mcp_server/__init__.py`
- `agent-verse-backend/app/tenancy/__init__.py`
- `agent-verse-backend/app/tenancy/context.py`
- `agent-verse-backend/app/tenancy/middleware.py`
- `agent-verse-backend/app/tenancy/rate_limiter.py`
- `agent-verse-backend/app/tenancy/store.py`

## Audit Trail

- EXTRACTED: 184 (96%)
- INFERRED: 7 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*