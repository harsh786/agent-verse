# Community 396

> 16 nodes · cohesion 0.19

## Key Concepts

- **import_openapi_connector()** (12 connections) — `agent-verse-backend/app/api/connectors.py`
- **openapi_importer.py** (9 connections) — `agent-verse-backend/app/mcp/openapi_importer.py`
- **extract_tools_from_spec** (8 connections) — `agent-verse-backend/app/mcp/openapi_importer.py`
- **import_and_register** (8 connections) — `agent-verse-backend/app/mcp/openapi_importer.py`
- **parse_openapi_spec** (6 connections) — `agent-verse-backend/app/mcp/openapi_importer.py`
- **persist_tools()** (6 connections) — `agent-verse-backend/app/mcp/openapi_importer.py`
- **Any** (4 connections)
- **_to_tool_name()** (3 connections) — `agent-verse-backend/app/mcp/openapi_importer.py`
- **AuthType enum** (2 connections) — `agent-verse-backend/app/mcp/registry.py`
- **Import an OpenAPI 3.x spec and register it as a connector with extracted tools.** (1 connections) — `agent-verse-backend/app/api/connectors.py`
- **OpenAPI 3.x spec importer — creates MCP connector registrations + tool…** (1 connections) — `agent-verse-backend/app/mcp/openapi_importer.py`
- **Convert HTTP method + path to a valid snake_case tool name.** (1 connections) — `agent-verse-backend/app/mcp/openapi_importer.py`
- **Persist tool definitions to tool_capabilities table. Returns count of tools…** (1 connections) — `agent-verse-backend/app/mcp/openapi_importer.py`
- **Import an OpenAPI spec and register it as a live MCP server. Args:…** (1 connections) — `agent-verse-backend/app/mcp/openapi_importer.py`
- **Parse OpenAPI 3.x spec from JSON or YAML string. Returns the parsed dict or…** (1 connections) — `agent-verse-backend/app/mcp/openapi_importer.py`
- **Extract tool definitions from OpenAPI 3.x paths. Returns list of tool dicts,…** (1 connections) — `agent-verse-backend/app/mcp/openapi_importer.py`

## Relationships

- [MCP A2A Protocol](MCP_A2A_Protocol.md) (6 shared connections)
- [Community 145](Community_145.md) (5 shared connections)
- [Community 182](Community_182.md) (3 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (2 shared connections)
- [Community 601](Community_601.md) (1 shared connections)
- [Community 82](Community_82.md) (1 shared connections)
- [Org Department Memory](Org_Department_Memory.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/api/connectors.py`
- `agent-verse-backend/app/mcp/openapi_importer.py`
- `agent-verse-backend/app/mcp/registry.py`

## Audit Trail

- EXTRACTED: 40 (95%)
- INFERRED: 2 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*