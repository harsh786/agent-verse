# Community 448

> 14 nodes · cohesion 0.18

## Key Concepts

- **StreamingGuard (rolling-buffer token filter)** (8 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`
- **.check_token()** (5 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`
- **streaming_guard.py** (3 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`
- **GuardDecision** (2 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`
- **.add_pattern()** (2 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`
- **._push()** (2 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`
- **.reset()** (2 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`
- **._window()** (2 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`
- **Streaming guardrail — mid-stream token-level content filter. Checks a rolling…** (1 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`
- **Rolling-buffer token-level content guard. Parameters ---------- patterns :…** (1 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`
- **Accumulate *token* in the rolling buffer and check patterns. Returns a…** (1 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`
- **Clear the rolling buffer between goals.** (1 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`
- **Dynamically add a pattern. Returns True on success.** (1 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/guardrails_v2/streaming_guard.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `agent-verse-backend/app/guardrails_v2/streaming_guard.py`

## Audit Trail

- EXTRACTED: 16 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*