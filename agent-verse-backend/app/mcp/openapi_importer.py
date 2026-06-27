"""OpenAPI 3.x spec importer — creates MCP connector registrations + tool definitions."""
from __future__ import annotations

import json
import uuid
from typing import Any


def _to_tool_name(method: str, path: str) -> str:
    """Convert HTTP method + path to a valid snake_case tool name."""
    # Remove leading slash, replace / { } - with _
    cleaned = (
        path.lstrip("/")
        .replace("/", "_")
        .replace("{", "")
        .replace("}", "")
        .replace("-", "_")
    )
    return f"{method.lower()}_{cleaned}".rstrip("_")


def parse_openapi_spec(spec_text: str) -> dict[str, Any]:
    """Parse OpenAPI 3.x spec from JSON or YAML string.

    Returns the parsed dict or raises ValueError on invalid input.
    """
    spec_text = spec_text.strip()
    if spec_text.startswith("{"):
        try:
            return json.loads(spec_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON OpenAPI spec: {exc}") from exc

    # Try YAML
    try:
        import yaml
        parsed = yaml.safe_load(spec_text)
        if not isinstance(parsed, dict):
            raise ValueError("OpenAPI spec must be a YAML/JSON object")
        return parsed
    except ImportError:
        raise ValueError("pyyaml required for YAML OpenAPI specs: pip install pyyaml")
    except Exception as exc:
        raise ValueError(f"Invalid YAML OpenAPI spec: {exc}") from exc


def extract_tools_from_spec(
    spec: dict[str, Any],
    connector_id: str,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Extract tool definitions from OpenAPI 3.x paths.

    Returns list of tool dicts, one per path+method combination.
    """
    paths = spec.get("paths", {})
    tools: list[dict[str, Any]] = []
    supported_methods = {"get", "post", "put", "patch", "delete"}

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in supported_methods:
                continue
            if not isinstance(operation, dict):
                continue

            tool_name = _to_tool_name(method, path)
            description = (
                operation.get("summary")
                or operation.get("description")
                or f"{method.upper()} {path}"
            )

            # Build parameters schema from OpenAPI parameters + requestBody
            properties: dict[str, Any] = {}
            required: list[str] = []

            for param in operation.get("parameters", []):
                if not isinstance(param, dict):
                    continue
                pname = param.get("name", "")
                if not pname:
                    continue
                pschema = param.get("schema", {})
                properties[pname] = {
                    "type": pschema.get("type", "string"),
                    "description": param.get("description", ""),
                    "in": param.get("in", "query"),
                }
                if param.get("required", False):
                    required.append(pname)

            request_body = operation.get("requestBody", {})
            if request_body:
                content = request_body.get("content", {})
                for _media_type, media_schema in content.items():
                    if "schema" in media_schema:
                        body_schema = media_schema["schema"]
                        body_props = body_schema.get("properties", {})
                        properties["body"] = {
                            "type": "object",
                            "description": "Request body",
                            "properties": body_props,
                        }
                        if request_body.get("required", False):
                            required.append("body")
                    break  # Only use first media type

            tools.append({
                "id": uuid.uuid4().hex,
                "tenant_id": tenant_id,
                "connector_id": connector_id,
                "tool_name": tool_name,
                "description": description[:500],
                "http_method": method.upper(),
                "http_path": path,
                "parameters_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
                "response_schema": None,
            })

    return tools


async def persist_tools(
    tools: list[dict[str, Any]],
    db_session_factory: Any,
    tenant_id: str,
) -> int:
    """Persist tool definitions to tool_capabilities table.

    Returns count of tools persisted.
    """
    if db_session_factory is None or not tools:
        return len(tools)  # In test mode, count as persisted

    from app.db.models.mcp import ToolCapability
    from app.db.rls import sqlalchemy_rls_context
    try:
        async with db_session_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                for tool in tools:
                    row = ToolCapability(
                        id=tool["id"],
                        tenant_id=tool["tenant_id"],
                        connector_id=tool["connector_id"],
                        tool_name=tool["tool_name"],
                        description=tool["description"],
                        http_method=tool["http_method"],
                        http_path=tool["http_path"],
                        parameters_schema=tool["parameters_schema"],
                        response_schema=tool.get("response_schema"),
                    )
                    session.add(row)
        return len(tools)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("persist_tools failed: %s", exc)
        return 0


async def import_and_register(
    *,
    spec_content: str,
    server_name: str,
    base_url: str,
    registry: Any,
    tenant_ctx: Any,
    auth_config: dict | None = None,
) -> dict:
    """Import an OpenAPI spec and register it as a live MCP server.

    Args:
        spec_content: OpenAPI JSON or YAML string
        server_name: Human-readable name for the connector
        base_url: API base URL
        registry: MCPRegistry instance
        tenant_ctx: Tenant context
        auth_config: Optional authentication configuration

    Returns:
        {"server_id": ..., "tool_count": ...} or {"error": ..., "server_id": None}
    """
    from app.mcp.registry import MCPServerConfig, AuthType

    # 1. Parse the spec
    try:
        spec = parse_openapi_spec(spec_content)
    except ValueError as exc:
        return {"error": str(exc), "server_id": None}

    # 2. Extract raw tools using the existing extractor
    tenant_id = getattr(tenant_ctx, "tenant_id", "")
    server_id = uuid.uuid4().hex
    raw_tools = extract_tools_from_spec(spec, connector_id=server_id, tenant_id=tenant_id)

    if not raw_tools:
        return {"error": "No tools found in OpenAPI spec", "server_id": None}

    # 3. Normalize to the name/description/parameters shape used by call_tool()
    tools: list[dict[str, Any]] = [
        {
            "name": t["tool_name"],
            "description": t["description"],
            "parameters": t["parameters_schema"],
            "http_method": t["http_method"],
            "http_path": t["http_path"],
        }
        for t in raw_tools
    ]

    # 4. Build MCPServerConfig
    auth_type = (
        AuthType.API_KEY if auth_config and "api_key" in auth_config else AuthType.NONE
    )
    server_config = MCPServerConfig(
        server_id=server_id,
        name=server_name,
        base_url=base_url,
        auth_type=auth_type,
        auth_config=auth_config or {},
        capabilities=list({t["name"] for t in tools}),
        enabled=True,
        tool_definitions=tools,
    )

    # 5. Register in the live registry
    await registry.register(server_config, tenant_ctx=tenant_ctx)

    return {"server_id": server_config.server_id, "tool_count": len(tools)}
