# Community 500

> 12 nodes · cohesion 0.17

## Key Concepts

- **IdempotencyStore** (9 connections) — `agent-verse-backend/app/reliability/idempotency.py`
- **idempotency.py** (2 connections) — `agent-verse-backend/app/reliability/idempotency.py`
- **.check_and_set()** (2 connections) — `agent-verse-backend/app/reliability/idempotency.py`
- **.exists()** (2 connections) — `agent-verse-backend/app/reliability/idempotency.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/reliability/idempotency.py`
- **.release()** (2 connections) — `agent-verse-backend/app/reliability/idempotency.py`
- **Any** (1 connections)
- **Redis-backed idempotency store for goal submissions.** (1 connections) — `agent-verse-backend/app/reliability/idempotency.py`
- **Return True if key is new (should process), False if duplicate.** (1 connections) — `agent-verse-backend/app/reliability/idempotency.py`
- **Release an idempotency key (e.g., if the request failed and should be retried).** (1 connections) — `agent-verse-backend/app/reliability/idempotency.py`
- **Check if key exists without setting it.** (1 connections) — `agent-verse-backend/app/reliability/idempotency.py`
- **Prevents duplicate goal submissions using Redis SET NX with TTL. Keyed by…** (1 connections) — `agent-verse-backend/app/reliability/idempotency.py`

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Community 436](Community_436.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/reliability/idempotency.py`

## Audit Trail

- EXTRACTED: 13 (93%)
- INFERRED: 1 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*