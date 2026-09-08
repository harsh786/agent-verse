# Community 251

> 25 nodes · cohesion 0.15

## Key Concepts

- **ToolResultCache** (18 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **Any** (8 connections)
- **.get()** (7 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **.set()** (7 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **tool_cache.py** (6 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **classify_tool** (6 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **._make_key()** (6 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **ttl_for_tool** (5 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **.get_stale()** (4 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **._inc()** (4 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **._l1_put()** (4 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **.set_with_stale()** (4 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **._get_stats()** (3 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **.stats()** (3 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **.invalidate_writes()** (2 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **Tool Result Cache ================= Caches MCP tool call responses with smart…** (1 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **Per-tenant MCP tool result cache backed by Redis. Plugs into…** (1 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **Return cached tool result or None.** (1 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **Cache a tool result. Silently ignores errors.** (1 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **Return ANY cached result regardless of expiry, up to max_age_seconds old. Used…** (1 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **Store both normal-TTL and long-lived stale backup.** (1 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **Invalidate cached results for related read tools after a write. e.g. after…** (1 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **Classify a tool as: 'write' | 'static' | 'read' | 'unknown' Used to determine…** (1 connections) — `agent-verse-backend/app/mcp/tool_cache.py`
- **Return TTL in seconds for a tool, or None if the tool should not be cached.…** (1 connections) — `agent-verse-backend/app/mcp/tool_cache.py`

## Relationships

- [MCP A2A Protocol](MCP_A2A_Protocol.md) (4 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/mcp/tool_cache.py`

## Audit Trail

- EXTRACTED: 53 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*