# Community 395

> 16 nodes · cohesion 0.16

## Key Concepts

- **CRDTRoomManager** (10 connections) — `agent-verse-backend/app/api/collab.py`
- **WebSocket** (7 connections)
- **.broadcast()** (4 connections) — `agent-verse-backend/app/api/collab.py`
- **.save_snapshot()** (3 connections) — `agent-verse-backend/app/api/collab.py`
- **.set_redis()** (3 connections) — `agent-verse-backend/app/api/collab.py`
- **.subscribe_redis()** (3 connections) — `agent-verse-backend/app/api/collab.py`
- **.join()** (2 connections) — `agent-verse-backend/app/api/collab.py`
- **.leave()** (2 connections) — `agent-verse-backend/app/api/collab.py`
- **.load_snapshot()** (2 connections) — `agent-verse-backend/app/api/collab.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/api/collab.py`
- **Manages Yjs CRDT room connections. Uses Redis pub/sub when available for cross-…** (1 connections) — `agent-verse-backend/app/api/collab.py`
- **Wire Redis client (called by app lifespan if Redis is configured).** (1 connections) — `agent-verse-backend/app/api/collab.py`
- **Save full Yjs document snapshot to Redis for late-joining peers.** (1 connections) — `agent-verse-backend/app/api/collab.py`
- **Load Yjs document snapshot for a new peer.** (1 connections) — `agent-verse-backend/app/api/collab.py`
- **Broadcast binary Yjs update to all peers in the room.** (1 connections) — `agent-verse-backend/app/api/collab.py`
- **Subscribe to Redis channel and forward messages to this WebSocket.** (1 connections) — `agent-verse-backend/app/api/collab.py`

## Relationships

- [Community 394](Community_394.md) (4 shared connections)
- [Community 369](Community_369.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/api/collab.py`

## Audit Trail

- EXTRACTED: 24 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*