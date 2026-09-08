# Community 427

> 15 nodes · cohesion 0.19

## Key Concepts

- **AuditFlusher (WAL->Postgres drainer, deprecated)** (11 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **.flush()** (6 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **._flush_batch()** (6 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **._ensure_chain_initialized()** (4 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **._release_flusher_lock()** (3 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **.run()** (3 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **._send_to_dlq()** (3 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **._try_acquire_flusher_lock()** (3 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Drains the Redis WAL into the ``audit_events`` Postgres table. Runs every…** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Seed the in-process chain tip from the DB for *tenant_id*. Called once per…** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Drain up to WAL_BATCH_SIZE events from Redis and insert to Postgres. Acquires a…** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Attempt SETNX on the flusher lock. Returns True if acquired.** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Release the flusher lock.** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Core flush implementation (called under the flusher lock).** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Background loop: flush WAL every WAL_FLUSH_INTERVAL seconds. Intended to be…** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`

## Relationships

- [Community 236](Community_236.md) (5 shared connections)
- [Community 286](Community_286.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/governance/audit_v2.py`

## Audit Trail

- EXTRACTED: 26 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*