# Community 472

> 13 nodes · cohesion 0.17

## Key Concepts

- **triggers.rate_limiter** (6 connections) — `agent-verse-backend/app/triggers/rate_limiter.py`
- **TriggerRateLimiter** (6 connections) — `agent-verse-backend/app/triggers/rate_limiter.py`
- **TriggerDispatcher.dispatch()** (4 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **effective_rate_cap()** (3 connections) — `agent-verse-backend/app/triggers/rate_limiter.py`
- **.check()** (3 connections) — `agent-verse-backend/app/triggers/rate_limiter.py`
- **TriggerDispatcher._create_goal()** (2 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **TriggerDispatcher._write_dlq()** (2 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **triggers.dlq module** (1 connections) — `agent-verse-backend/app/triggers/dlq.py`
- **Trigger rate limiter using Redis INCR with TTL sliding window.** (1 connections) — `agent-verse-backend/app/triggers/rate_limiter.py`
- **Return the effective per-hour cap: min(user setting, plan ceiling).** (1 connections) — `agent-verse-backend/app/triggers/rate_limiter.py`
- **Redis-backed sliding window rate limiter for trigger firings.** (1 connections) — `agent-verse-backend/app/triggers/rate_limiter.py`
- **Return True if the trigger is allowed to fire, False if rate-limited.** (1 connections) — `agent-verse-backend/app/triggers/rate_limiter.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/triggers/rate_limiter.py`

## Relationships

- [Community 79](Community_79.md) (4 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/triggers/dispatcher.py`
- `agent-verse-backend/app/triggers/dlq.py`
- `agent-verse-backend/app/triggers/rate_limiter.py`

## Audit Trail

- EXTRACTED: 15 (79%)
- INFERRED: 4 (21%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*