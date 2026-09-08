# MCP A2A Protocol

> 118 nodes · cohesion 0.03

## Key Concepts

- **client.py** (33 connections) — `agent-verse-backend/app/mcp/client.py`
- **MCPClient** (33 connections) — `agent-verse-backend/app/mcp/client.py`
- **MCPServerConfig** (32 connections) — `agent-verse-backend/app/mcp/registry.py`
- **MCPRegistry** (26 connections) — `agent-verse-backend/app/mcp/registry.py`
- **._call_tool_impl()** (20 connections) — `agent-verse-backend/app/mcp/client.py`
- **.call_tool()** (17 connections) — `agent-verse-backend/app/mcp/client.py`
- **.discover_tools()** (16 connections) — `agent-verse-backend/app/mcp/client.py`
- **MCPWebSocketClient** (15 connections) — `agent-verse-backend/app/mcp/ws_client.py`
- **mcp/registry.py** (13 connections) — `agent-verse-backend/app/mcp/registry.py`
- **Any** (11 connections)
- **mcp/__init__.py** (11 connections) — `agent-verse-backend/app/mcp/__init__.py`
- **._dispatch_builtin_tool()** (10 connections) — `agent-verse-backend/app/mcp/client.py`
- **._build_auth_headers()** (9 connections) — `agent-verse-backend/app/mcp/client.py`
- **._dispatch_jira_rest_tool()** (8 connections) — `agent-verse-backend/app/mcp/client.py`
- **.update()** (8 connections) — `agent-verse-backend/app/mcp/registry.py`
- **._dispatch_openapi_tool()** (7 connections) — `agent-verse-backend/app/mcp/client.py`
- **ToolCallResult** (7 connections) — `agent-verse-backend/app/mcp/client.py`
- **.list_server_records()** (7 connections) — `agent-verse-backend/app/mcp/registry.py`
- **_absolute_http_url()** (6 connections) — `agent-verse-backend/app/mcp/client.py`
- **MCPClient.call_tool** (6 connections) — `agent-verse-backend/app/mcp/client.py`
- **MCPClient._call_tool_impl** (6 connections) — `agent-verse-backend/app/mcp/client.py`
- **_is_jira_rest_endpoint()** (6 connections) — `agent-verse-backend/app/mcp/client.py`
- **._ensure_mcp_session()** (6 connections) — `agent-verse-backend/app/mcp/client.py`
- **._get_circuit_breaker()** (6 connections) — `agent-verse-backend/app/mcp/client.py`
- **.get()** (6 connections) — `agent-verse-backend/app/mcp/registry.py`
- *... and 93 more nodes in this community*

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (17 shared connections)
- [Community 145](Community_145.md) (9 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (8 shared connections)
- [Community 199](Community_199.md) (6 shared connections)
- [Community 95](Community_95.md) (6 shared connections)
- [Community 279](Community_279.md) (6 shared connections)
- [Community 396](Community_396.md) (6 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (6 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (5 shared connections)
- [Community 251](Community_251.md) (4 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (4 shared connections)
- [Community 255](Community_255.md) (3 shared connections)

## Source Files

- `agent-verse-backend/app/mcp/__init__.py`
- `agent-verse-backend/app/mcp/a2a.py`
- `agent-verse-backend/app/mcp/catalog.py`
- `agent-verse-backend/app/mcp/client.py`
- `agent-verse-backend/app/mcp/registry.py`
- `agent-verse-backend/app/mcp/ws_client.py`
- `agent-verse-backend/app/reliability/tool_inverses.py`

## Audit Trail

- EXTRACTED: 310 (96%)
- INFERRED: 11 (3%)
- AMBIGUOUS: 1 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*