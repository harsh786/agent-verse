# Community 331

> 20 nodes · cohesion 0.17

## Key Concepts

- **RPASessionStore (Redis-backed, 24h TTL)** (17 connections) — `agent-verse-backend/app/rpa/session.py`
- **RPAManagedSession** (8 connections) — `agent-verse-backend/app/rpa/session.py`
- **rpa/session.py** (6 connections) — `agent-verse-backend/app/rpa/session.py`
- **._redis_save()** (6 connections) — `agent-verse-backend/app/rpa/session.py`
- **.get()** (5 connections) — `agent-verse-backend/app/rpa/session.py`
- **.list_active()** (5 connections) — `agent-verse-backend/app/rpa/session.py`
- **._redis_load()** (5 connections) — `agent-verse-backend/app/rpa/session.py`
- **.close()** (4 connections) — `agent-verse-backend/app/rpa/session.py`
- **.create()** (4 connections) — `agent-verse-backend/app/rpa/session.py`
- **._skey()** (3 connections) — `agent-verse-backend/app/rpa/session.py`
- **._tkey()** (3 connections) — `agent-verse-backend/app/rpa/session.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/rpa/session.py`
- **Any** (1 connections)
- **RPA session state shared by agents and runner adapters.** (1 connections) — `agent-verse-backend/app/rpa/session.py`
- **Return all active sessions for a tenant.** (1 connections) — `agent-verse-backend/app/rpa/session.py`
- **Mark a session as closed.** (1 connections) — `agent-verse-backend/app/rpa/session.py`
- **Lightweight session record tracked by RPASessionStore for API consumers.** (1 connections) — `agent-verse-backend/app/rpa/session.py`
- **Redis-backed RPA session store with 24-hour TTL. Per-session key:…** (1 connections) — `agent-verse-backend/app/rpa/session.py`
- **Create and persist a new active RPA session.** (1 connections) — `agent-verse-backend/app/rpa/session.py`
- **Retrieve a session by ID, scoped to the given tenant.** (1 connections) — `agent-verse-backend/app/rpa/session.py`

## Relationships

- [Community 134](Community_134.md) (5 shared connections)
- [Community 222](Community_222.md) (3 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/rpa/session.py`

## Audit Trail

- EXTRACTED: 41 (95%)
- INFERRED: 2 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*