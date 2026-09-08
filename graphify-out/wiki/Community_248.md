# Community 248

> 25 nodes · cohesion 0.10

## Key Concepts

- **secrets.py** (19 connections) — `agent-verse-backend/app/core/secrets.py`
- **AgentCredentialStore** (8 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **agent_credentials.py** (7 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **emarsys_server.py** (7 connections) — `agent-verse-backend/app/mcp/servers/emarsys_server.py`
- **.create_key()** (4 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **.resolve()** (4 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **Any** (4 connections)
- **.check_tool_allowed()** (3 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **generate_agent_api_key()** (3 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **is_agent_key()** (3 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **call_tool()** (3 connections) — `agent-verse-backend/app/mcp/servers/emarsys_server.py`
- **.list_for_agent()** (2 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **_wsse_header()** (2 connections) — `agent-verse-backend/app/mcp/servers/emarsys_server.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **.revoke()** (1 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **Per-Agent Credential System ============================ Every agent can have…** (1 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **Check if this key's policy allows the given tool.** (1 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **Generate (raw_key, key_hash) for an agent-scoped API key.** (1 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **Return True if this key is an agent-scoped key.** (1 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **Per-agent API key store. Agent keys: - Are scoped to one agent_id - Can only…** (1 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **Create a new agent-scoped API key. Returns {key_id, raw_key, ...}.** (1 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **Validate key and return record, or None if invalid/expired.** (1 connections) — `agent-verse-backend/app/auth/agent_credentials.py`
- **Secret resolution that works identically in dev (env vars) and prod (mounted…** (1 connections) — `agent-verse-backend/app/core/secrets.py`
- **Any** (1 connections)
- **Emarsys MCP server — marketing platform with contacts, campaigns, and…** (1 connections) — `agent-verse-backend/app/mcp/servers/emarsys_server.py`

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (4 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (3 shared connections)
- [Community 279](Community_279.md) (2 shared connections)
- [Community 394](Community_394.md) (1 shared connections)
- [Community 63](Community_63.md) (1 shared connections)
- [Community 112](Community_112.md) (1 shared connections)
- [Tenant API Keys/IP Allowlist](Tenant_API_Keys-IP_Allowlist.md) (1 shared connections)
- [Community 100](Community_100.md) (1 shared connections)
- [Community 184](Community_184.md) (1 shared connections)
- [Community 101](Community_101.md) (1 shared connections)
- [Community 262](Community_262.md) (1 shared connections)
- [Community 186](Community_186.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/auth/agent_credentials.py`
- `agent-verse-backend/app/core/secrets.py`
- `agent-verse-backend/app/mcp/servers/emarsys_server.py`

## Audit Trail

- EXTRACTED: 51 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*