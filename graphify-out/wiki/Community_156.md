# Community 156

> 34 nodes · cohesion 0.14

## Key Concepts

- **ScheduleStore** (33 connections) — `agent-verse-backend/app/triggers/store.py`
- **Any** (12 connections)
- **Schedule** (10 connections) — `agent-verse-backend/app/db/models/scheduling.py`
- **._write_redis_schedule()** (9 connections) — `agent-verse-backend/app/triggers/store.py`
- **.create_async()** (6 connections) — `agent-verse-backend/app/triggers/store.py`
- **._db_create()** (6 connections) — `agent-verse-backend/app/triggers/store.py`
- **._write_redis_schedule_async()** (6 connections) — `agent-verse-backend/app/triggers/store.py`
- **._await_redis_call()** (5 connections) — `agent-verse-backend/app/triggers/store.py`
- **.create()** (5 connections) — `agent-verse-backend/app/triggers/store.py`
- **._db_delete_schedule()** (5 connections) — `agent-verse-backend/app/triggers/store.py`
- **.get()** (5 connections) — `agent-verse-backend/app/triggers/store.py`
- **.pause()** (5 connections) — `agent-verse-backend/app/triggers/store.py`
- **._redis_call()** (5 connections) — `agent-verse-backend/app/triggers/store.py`
- **._redis_key()** (5 connections) — `agent-verse-backend/app/triggers/store.py`
- **._redis_payload()** (5 connections) — `agent-verse-backend/app/triggers/store.py`
- **.resume()** (5 connections) — `agent-verse-backend/app/triggers/store.py`
- **.sync_from_db()** (5 connections) — `agent-verse-backend/app/triggers/store.py`
- **TriggerSpec** (4 connections)
- **._db_update_paused()** (4 connections) — `agent-verse-backend/app/triggers/store.py`
- **.delete()** (4 connections) — `agent-verse-backend/app/triggers/store.py`
- **.delete_async()** (4 connections) — `agent-verse-backend/app/triggers/store.py`
- **._delete_redis_schedule()** (4 connections) — `agent-verse-backend/app/triggers/store.py`
- **._delete_redis_schedule_async()** (4 connections) — `agent-verse-backend/app/triggers/store.py`
- **.find_by_type()** (4 connections) — `agent-verse-backend/app/triggers/store.py`
- **.find_by_type_async()** (4 connections) — `agent-verse-backend/app/triggers/store.py`
- *... and 9 more nodes in this community*

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (8 shared connections)
- [Community 210](Community_210.md) (5 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (4 shared connections)
- [Chat DB Models](Chat_DB_Models.md) (3 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (3 shared connections)
- [Community 112](Community_112.md) (2 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/db/models/scheduling.py`
- `agent-verse-backend/app/triggers/store.py`

## Audit Trail

- EXTRACTED: 99 (95%)
- INFERRED: 5 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*