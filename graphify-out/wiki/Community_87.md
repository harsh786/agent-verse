# Community 87

> 48 nodes · cohesion 0.06

## Key Concepts

- **Settings (pydantic-settings)** (20 connections) — `agent-verse-backend/app/core/config.py`
- **pools.py** (14 connections) — `agent-verse-backend/app/core/pools.py`
- **lifespan()** (11 connections) — `agent-verse-backend/app/main.py`
- **ConnectionPools** (10 connections) — `agent-verse-backend/app/core/pools.py`
- **config.py** (9 connections) — `agent-verse-backend/app/core/config.py`
- **.__init__()** (8 connections) — `agent-verse-backend/app/core/pools.py`
- **observability/health.py** (6 connections) — `agent-verse-backend/app/observability/health.py`
- **HealthRegistry** (6 connections) — `agent-verse-backend/app/observability/health.py`
- **Any** (5 connections)
- **_default_http_factory()** (4 connections) — `agent-verse-backend/app/core/pools.py`
- **_default_pg_factory()** (4 connections) — `agent-verse-backend/app/core/pools.py`
- **_default_redis_factory()** (4 connections) — `agent-verse-backend/app/core/pools.py`
- **HealthCheck** (4 connections) — `agent-verse-backend/app/observability/health.py`
- **._split_csv_origins()** (3 connections) — `agent-verse-backend/app/core/config.py`
- **.run()** (3 connections) — `agent-verse-backend/app/observability/health.py`
- **.is_sso_production_safe()** (2 connections) — `agent-verse-backend/app/core/config.py`
- **._validate_repository_lease_margin()** (2 connections) — `agent-verse-backend/app/core/config.py`
- **.health_checks()** (2 connections) — `agent-verse-backend/app/core/pools.py`
- **_default_pg_ping()** (2 connections) — `agent-verse-backend/app/core/pools.py`
- **_default_redis_ping()** (2 connections) — `agent-verse-backend/app/core/pools.py`
- **ConnectionPools.health_checks()** (2 connections) — `agent-verse-backend/app/core/pools.py`
- **ConnectionPools.startup()** (2 connections) — `agent-verse-backend/app/core/pools.py`
- **.register()** (2 connections) — `agent-verse-backend/app/observability/health.py`
- **celery_app (Celery instance)** (2 connections) — `agent-verse-backend/app/scaling/celery_app.py`
- **GoalService.sync_from_db()** (2 connections) — `agent-verse-backend/app/services/goal_service.py`
- *... and 23 more nodes in this community*

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (11 shared connections)
- [Community 136](Community_136.md) (4 shared connections)
- [Community 102](Community_102.md) (2 shared connections)
- [Community 100](Community_100.md) (2 shared connections)
- [Knowledge Ingestion API](Knowledge_Ingestion_API.md) (1 shared connections)
- [Community 758](Community_758.md) (1 shared connections)
- [Community 247](Community_247.md) (1 shared connections)
- [Community 279](Community_279.md) (1 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Community 152](Community_152.md) (1 shared connections)
- [Community 438](Community_438.md) (1 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/core/config.py`
- `agent-verse-backend/app/core/pools.py`
- `agent-verse-backend/app/main.py`
- `agent-verse-backend/app/observability/health.py`
- `agent-verse-backend/app/scaling/celery_app.py`
- `agent-verse-backend/app/services/goal_service.py`
- `agent-verse-backend/app/services/tenant_service.py`
- `agent-verse-backend/app/triggers/store.py`

## Audit Trail

- EXTRACTED: 82 (90%)
- INFERRED: 7 (8%)
- AMBIGUOUS: 2 (2%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*