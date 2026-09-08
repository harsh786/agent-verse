# Community 247

> 25 nodes · cohesion 0.14

## Key Concepts

- **get_session_factory()** (42 connections) — `agent-verse-backend/app/db/session.py`
- **db/session.py** (10 connections) — `agent-verse-backend/app/db/session.py`
- **api/replay.py** (9 connections) — `agent-verse-backend/app/api/replay.py`
- **replay_goal()** (8 connections) — `agent-verse-backend/app/api/replay.py`
- **goal_timeline()** (6 connections) — `agent-verse-backend/app/api/replay.py`
- **_make_session_factory()** (6 connections) — `agent-verse-backend/app/db/session.py`
- **_get_db()** (5 connections) — `agent-verse-backend/app/api/replay.py`
- **get_db_session()** (5 connections) — `agent-verse-backend/app/db/session.py`
- **_make_engine()** (5 connections) — `agent-verse-backend/app/db/session.py`
- **Any** (4 connections)
- **Request** (4 connections)
- **_require_tenant()** (4 connections) — `agent-verse-backend/app/api/replay.py`
- **dispose_task_engine()** (4 connections) — `agent-verse-backend/app/db/session.py`
- **get_db()** (4 connections) — `agent-verse-backend/app/db/session.py`
- **AsyncSession** (4 connections)
- **get** (2 connections)
- **async_sessionmaker** (2 connections)
- **Goal execution replay API. Provides step-by-step reconstruction of a completed…** (1 connections) — `agent-verse-backend/app/api/replay.py`
- **Get a compact chronological timeline of goal events for visualization.** (1 connections) — `agent-verse-backend/app/api/replay.py`
- **Reconstruct the full execution timeline of a completed goal. Returns a…** (1 connections) — `agent-verse-backend/app/api/replay.py`
- **Async SQLAlchemy session factory and FastAPI dependency.** (1 connections) — `agent-verse-backend/app/db/session.py`
- **Dispose all pooled asyncpg connections owned by the module-level engine. Celery…** (1 connections) — `agent-verse-backend/app/db/session.py`
- **Context-manager that yields an AsyncSession and commits/rolls back.** (1 connections) — `agent-verse-backend/app/db/session.py`
- **FastAPI dependency — yields one session per request.** (1 connections) — `agent-verse-backend/app/db/session.py`
- **AsyncEngine** (1 connections)

## Relationships

- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (11 shared connections)
- [Community 51](Community_51.md) (5 shared connections)
- [Community 136](Community_136.md) (2 shared connections)
- [Community 57](Community_57.md) (2 shared connections)
- [Community 67](Community_67.md) (2 shared connections)
- [Community 182](Community_182.md) (2 shared connections)
- [Community 354](Community_354.md) (2 shared connections)
- [Enterprise API Surface](Enterprise_API_Surface.md) (2 shared connections)
- [Community 118](Community_118.md) (2 shared connections)
- [Community 86](Community_86.md) (2 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Community 84](Community_84.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/api/replay.py`
- `agent-verse-backend/app/db/session.py`

## Audit Trail

- EXTRACTED: 89 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*