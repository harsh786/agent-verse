# Community 208

> 29 nodes · cohesion 0.10

## Key Concepts

- **BrowserSessionManager (live Playwright sessions)** (17 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **BrowserSession** (9 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **session_manager.py** (6 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **.get_or_create()** (5 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **Any** (5 connections)
- **._deregister_from_redis()** (4 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **.get_page()** (4 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **.list_active()** (4 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **.list_active_from_redis()** (4 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **._register_in_redis()** (4 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **.page()** (3 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **.cleanup_expired()** (3 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **.close()** (3 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **._create_session()** (3 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **.close()** (2 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **.is_alive()** (1 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **.touch()** (1 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **Browser session manager — keeps Playwright sessions alive across multiple RPA…** (1 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **Close a specific session.** (1 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **Close sessions idle longer than max_idle_seconds.** (1 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **List all active sessions, optionally filtered by tenant.** (1 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **Persist session metadata to Redis for visibility across restarts.** (1 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **Remove session metadata from Redis on close.** (1 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- **List active sessions persisted in Redis (survives restarts).** (1 connections) — `agent-verse-backend/app/rpa/session_manager.py`
- *... and 4 more nodes in this community*

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (3 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 437](Community_437.md) (1 shared connections)
- [Community 134](Community_134.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/rpa/session_manager.py`

## Audit Trail

- EXTRACTED: 47 (96%)
- INFERRED: 2 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*