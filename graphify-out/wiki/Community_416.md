# Community 416

> 15 nodes · cohesion 0.14

## Key Concepts

- **_CollabPubSub** (9 connections) — `agent-verse-backend/app/api/collab.py`
- **.ensure_started()** (3 connections) — `agent-verse-backend/app/api/collab.py`
- **._listener_loop()** (3 connections) — `agent-verse-backend/app/api/collab.py`
- **.publish()** (3 connections) — `agent-verse-backend/app/api/collab.py`
- **.get_participant_count()** (2 connections) — `agent-verse-backend/app/api/collab.py`
- **.track_join()** (2 connections) — `agent-verse-backend/app/api/collab.py`
- **.track_leave()** (2 connections) — `agent-verse-backend/app/api/collab.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/api/collab.py`
- **Increment cross-replica participant counter in Redis.** (1 connections) — `agent-verse-backend/app/api/collab.py`
- **Decrement cross-replica participant counter in Redis.** (1 connections) — `agent-verse-backend/app/api/collab.py`
- **Return participant count, preferring Redis for cross-replica accuracy.** (1 connections) — `agent-verse-backend/app/api/collab.py`
- **Subscribe to ``collab:*`` and forward messages to local WS connections.** (1 connections) — `agent-verse-backend/app/api/collab.py`
- **Redis pub/sub fanout for cross-replica WebSocket broadcast. When a message…** (1 connections) — `agent-verse-backend/app/api/collab.py`
- **Lazily start the subscriber task on first WebSocket connection.** (1 connections) — `agent-verse-backend/app/api/collab.py`
- **Publish a message to all replicas for the given session. No-op (silently) when…** (1 connections) — `agent-verse-backend/app/api/collab.py`

## Relationships

- [Community 394](Community_394.md) (1 shared connections)
- [Community 369](Community_369.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/api/collab.py`

## Audit Trail

- EXTRACTED: 17 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*