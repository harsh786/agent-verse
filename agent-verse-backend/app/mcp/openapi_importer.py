"""OpenAPI 3.x spec importer — creates MCP connector registrations + tool definitions."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.mcp.registry import AuthType


def _to_tool_name(method: str, path: str) -> str:
    """Convert HTTP method + path to a valid snake_case tool name."""
    # Remove leading slash, replace / { } - with _
    cleaned = path.lstrip("/").replace("/", "_").replace("{", "").replace("}", "").replace("-", "_")
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
    except ImportError as _b904_exc:
        raise ValueError("pyyaml required for YAML OpenAPI specs: pip install pyyaml") from _b904_exc  # noqa: E501
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

            tools.append(
                {
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
                }
            )

    return tools


async def persist_tools(
    tools: list[dict[str, Any]],
    db_session_factory: Any,
    tenant_id: str,
) -> int:
    """Persist tool definitions to tool_capabilities table.

    Returns count of tools persisted.

    Note: there is no SQLAlchemy ORM model for ``tool_capabilities`` anywhere
    in ``app/db/models/`` -- every other read/write path against this table
    (``app/api/connectors.py::list_capabilities``/``discover_connector_tools``,
    ``app/mcp/client.py::_update_tool_stats``) uses raw SQL via ``text()``.
    This mirrors that established pattern instead of importing a
    ``ToolCapability`` ORM class that does not exist.
    """
    if db_session_factory is None or not tools:
        return len(tools)  # In test mode, count as persisted

    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with (
            db_session_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            for tool in tools:
                response_schema = tool.get("response_schema")
                await session.execute(
                    text(
                        """
                        INSERT INTO tool_capabilities
                            (id, tenant_id, connector_id, tool_name,
                             description, http_method, http_path,
                             parameters_schema, response_schema)
                        VALUES
                            (:id, :tid, :cid, :name,
                             :desc, :method, :path,
                             CAST(:params AS json), CAST(:response AS json))
                        ON CONFLICT (tenant_id, connector_id, tool_name) DO NOTHING
                        """
                    ),
                    {
                        "id": tool["id"],
                        "tid": tool["tenant_id"],
                        "cid": tool["connector_id"],
                        "name": tool["tool_name"],
                        "desc": tool["description"],
                        "method": tool["http_method"],
                        "path": tool["http_path"],
                        "params": json.dumps(tool["parameters_schema"]),
                        "response": json.dumps(response_schema)
                        if response_schema is not None
                        else None,
                    },
                )
        return len(tools)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("persist_tools failed: %s", exc)
        return 0


def _primary_security_scheme(spec: dict[str, Any]) -> dict[str, Any]:
    """The scheme named by the spec's top-level ``security``, else the first one."""
    components = spec.get("components")
    schemes = components.get("securitySchemes") if isinstance(components, dict) else None
    if not isinstance(schemes, dict) or not schemes:
        return {}
    for requirement in spec.get("security") or []:
        if isinstance(requirement, dict):
            for name in requirement:
                scheme = schemes.get(name)
                if isinstance(scheme, dict):
                    return scheme
    first = next(iter(schemes.values()))
    return first if isinstance(first, dict) else {}


def resolve_auth_from_spec(
    spec: dict[str, Any], auth_config: dict[str, Any]
) -> tuple[AuthType, dict[str, Any]]:
    """Map the caller's credential + the spec's securityScheme to (AuthType, auth_config).

    The credential stays whatever the caller supplied (``api_key`` / ``token`` /
    ``username``+``password``, possibly a vault ref). The spec only decides the
    type and where it goes (``header_name`` / ``in`` / ``param_name``); values the
    caller set explicitly always win.
    """
    from app.mcp.registry import AuthType

    cfg = dict(auth_config)
    secret = cfg.get("api_key") or cfg.get("token")
    scheme = _primary_security_scheme(spec)
    stype = str(scheme.get("type", "")).lower()
    if secret and stype == "apikey":
        cfg.setdefault("api_key", secret)
        location = str(scheme.get("in", "header")).lower()
        name = str(scheme.get("name", "") or "")
        cfg.setdefault("in", location)
        if name:
            cfg.setdefault("header_name" if location == "header" else "param_name", name)
        return AuthType.API_KEY, cfg
    is_basic = stype == "http" and str(scheme.get("scheme", "")).lower() == "basic"
    if is_basic and cfg.get("username"):
        return AuthType.BASIC, cfg
    if secret and stype in {"http", "oauth2", "openidconnect"}:
        cfg.setdefault("token", secret)
        return AuthType.BEARER, cfg
    # No usable scheme in the spec: infer from the credential the caller gave.
    if "api_key" in cfg:
        return AuthType.API_KEY, cfg
    if "token" in cfg:
        return AuthType.BEARER, cfg
    if cfg.get("username"):
        return AuthType.BASIC, cfg
    return AuthType.NONE, cfg


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
    from app.mcp.registry import MCPServerConfig

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

    # 4. Build MCPServerConfig. Auth type and placement come from the spec's
    # securitySchemes (previously ignored: only an "api_key" entry was noticed,
    # always sent as X-API-Key, and a bearer "token" left auth_type=NONE).
    auth_type, resolved_auth = resolve_auth_from_spec(spec, auth_config or {})
    server_config = MCPServerConfig(
        server_id=server_id,
        name=server_name,
        base_url=base_url,
        auth_type=auth_type,
        auth_config=resolved_auth,
        capabilities=list({t["name"] for t in tools}),
        enabled=True,
        tool_definitions=tools,
    )

    # 5. Register in the live registry
    await registry.register(server_config, tenant_ctx=tenant_ctx)

    return {"server_id": server_config.server_id, "tool_count": len(tools)}
