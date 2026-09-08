# Community 436

> 15 nodes · cohesion 0.13

## Key Concepts

- **GoalExecutionLock** (9 connections) — `agent-verse-backend/app/reliability/distributed_lock.py`
- **distributed_lock.py** (2 connections) — `agent-verse-backend/app/reliability/distributed_lock.py`
- **.acquire()** (2 connections) — `agent-verse-backend/app/reliability/distributed_lock.py`
- **.extend()** (2 connections) — `agent-verse-backend/app/reliability/distributed_lock.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/reliability/distributed_lock.py`
- **.is_locked()** (2 connections) — `agent-verse-backend/app/reliability/distributed_lock.py`
- **.release()** (2 connections) — `agent-verse-backend/app/reliability/distributed_lock.py`
- **Any** (1 connections)
- **Redis-backed distributed lock for at-most-once goal execution.** (1 connections) — `agent-verse-backend/app/reliability/distributed_lock.py`
- **Redis SET NX PX lock ensuring at-most-once execution per cluster. Uses a Lua…** (1 connections) — `agent-verse-backend/app/reliability/distributed_lock.py`
- **Returns True if lock acquired, False if another worker holds it.** (1 connections) — `agent-verse-backend/app/reliability/distributed_lock.py`
- **Release lock only if we own it (Lua atomic check-and-delete).** (1 connections) — `agent-verse-backend/app/reliability/distributed_lock.py`
- **Extend TTL if we still own the lock.** (1 connections) — `agent-verse-backend/app/reliability/distributed_lock.py`
- **Check if any worker holds a lock for this goal.** (1 connections) — `agent-verse-backend/app/reliability/distributed_lock.py`
- **goal_lifecycle pause/cancel signals** (1 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`

## Relationships

- [Community 500](Community_500.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/reliability/distributed_lock.py`
- `agent-verse-backend/app/reliability/goal_lifecycle.py`

## Audit Trail

- EXTRACTED: 13 (87%)
- INFERRED: 2 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*