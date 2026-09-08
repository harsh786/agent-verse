# Community 661

> 7 nodes · cohesion 0.29

## Key Concepts

- **_SyncGoalLock (distributed at-most-once lock)** (6 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **.acquire()** (2 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **.release()** (2 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **Synchronous Redis-based distributed lock for Celery tasks. Uses ``SET NX PX``…** (1 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **Return True if the lock was acquired; False if another worker holds it.** (1 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **Release the lock only if this instance owns it (atomic Lua check-and-delete).** (1 connections) — `agent-verse-backend/app/scaling/tasks.py`

## Relationships

- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (2 shared connections)
- [Community 102](Community_102.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/scaling/tasks.py`

## Audit Trail

- EXTRACTED: 9 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*