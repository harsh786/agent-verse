# Community 403

> 16 nodes · cohesion 0.18

## Key Concepts

- **ChannelRateLimiter** (6 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`
- **.check_and_increment()** (6 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`
- **gateway/rate_limiter.py** (5 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`
- **RateLimitExceeded** (5 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`
- **._check_memory()** (4 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`
- **._check_redis()** (4 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`
- **Any** (4 connections)
- **.__init__()** (3 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`
- **Exception** (1 connections)
- **Per-channel rate limiter — Q11 of spec. Limits: Per tenant across all channels:…** (1 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`
- **Redis sliding window (ZADD + ZCOUNT pattern).** (1 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`
- **In-memory sliding window fallback.** (1 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`
- **Raised when a channel command exceeds rate limits.** (1 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`
- **Sliding window rate limiter for gateway channels. Uses Redis when available;…** (1 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`
- **Check rate limits and increment counter. Returns: {"allowed": bool,…** (1 connections) — `agent-verse-backend/app/gateway/rate_limiter.py`

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/gateway/rate_limiter.py`

## Audit Trail

- EXTRACTED: 24 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*