# Community 372

> 17 nodes · cohesion 0.18

## Key Concepts

- **stream.py** (9 connections) — `agent-verse-backend/app/chat/stream.py`
- **_sse()** (9 connections) — `agent-verse-backend/app/chat/stream.py`
- **stream_goal_progress()** (5 connections) — `agent-verse-backend/app/chat/stream.py`
- **stream_qa_response()** (5 connections) — `agent-verse-backend/app/chat/stream.py`
- **stream_clarify()** (4 connections) — `agent-verse-backend/app/chat/stream.py`
- **stream_schedule_created()** (4 connections) — `agent-verse-backend/app/chat/stream.py`
- **Any** (3 connections)
- **stream_artifact_created()** (3 connections) — `agent-verse-backend/app/chat/stream.py`
- **stream_hitl()** (3 connections) — `agent-verse-backend/app/chat/stream.py`
- **SSE stream generator for the chat feature. Multiplexes two sources: 1. LLM…** (1 connections) — `agent-verse-backend/app/chat/stream.py`
- **Emit a clarify_needed event.** (1 connections) — `agent-verse-backend/app/chat/stream.py`
- **Emit a hitl_required event — pauses goal execution until approved.** (1 connections) — `agent-verse-backend/app/chat/stream.py`
- **Emit a schedule_created confirmation event.** (1 connections) — `agent-verse-backend/app/chat/stream.py`
- **Emit an artifact_created event.** (1 connections) — `agent-verse-backend/app/chat/stream.py`
- **Format a single SSE message.** (1 connections) — `agent-verse-backend/app/chat/stream.py`
- **Simulate QA streaming — yields SSE events for each token. In production this is…** (1 connections) — `agent-verse-backend/app/chat/stream.py`
- **Simulate GOAL streaming — yields step progress events. In production this…** (1 connections) — `agent-verse-backend/app/chat/stream.py`

## Relationships

- [Community 48](Community_48.md) (5 shared connections)

## Source Files

- `agent-verse-backend/app/chat/stream.py`

## Audit Trail

- EXTRACTED: 29 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*