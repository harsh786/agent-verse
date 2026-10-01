"""Looker MCP server — Looker Business Intelligence.

Environment:
  LOOKER_BASE_URL:  https://your-instance.looker.com
  LOOKER_CLIENT_ID: Looker API3 client ID
  LOOKER_CLIENT_SECRET: Looker API3 client secret
"""

from __future__ import annotations

from typing import Any

import httpx

from app.mcp.servers.credentials import tenant_getenv
from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "looker_run_look",
        "description": "Run a saved Looker Look and return the results",
        "parameters": {
            "type": "object",
            "properties": {
                "look_id": {"type": "integer", "description": "ID of the Look to run"},
                "result_format": {
                    "type": "string",
                    "enum": ["json", "csv", "json_detail"],
                    "default": "json",
                },
                "limit": {"type": "integer", "default": 500},
            },
            "required": ["look_id"],
        },
    },
    {
        "name": "looker_list_dashboards",
        "description": "List available Looker dashboards",
        "parameters": {
            "type": "object",
            "properties": {
                "fields": {"type": "string", "description": "Comma-separated fields to return"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "looker_query_model",
        "description": "Run an inline Looker query against a model/explore",
        "parameters": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "LookML model name"},
                "explore": {"type": "string", "description": "Explore name"},
                "dimensions": {"type": "array", "items": {"type": "string"}},
                "measures": {"type": "array", "items": {"type": "string"}},
                "filters": {"type": "object", "description": "Field → value filter map"},
                "limit": {"type": "integer", "default": 500},
            },
            "required": ["model", "explore"],
        },
    },
    {
        "name": "looker_list_explores",
        "description": "List all explores in a given Looker model",
        "parameters": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
            },
            "required": ["model"],
        },
    },
]

# Per-connection token cache keyed by (base URL, client id, secret digest): it
# used to be ONE dict for every caller, so a tenant could be served another
# tenant's Looker token.
_token_cache: dict[tuple[str, str, str], tuple[str, float]] = {}


async def _get_token(
    client: httpx.AsyncClient, base_url: str, client_id: str, client_secret: str
) -> str:
    """Obtain a Looker API bearer token for these credentials (cached per connection)."""
    import hashlib
    import time

    key = (base_url, client_id, hashlib.sha256(client_secret.encode()).hexdigest())
    cached = _token_cache.get(key)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    resp = await client.post(
        f"{base_url}/api/4.0/login",
        data={"client_id": client_id, "client_secret": client_secret},
    )
    resp.raise_for_status()
    data = resp.json()
    token = str(data["access_token"])
    _token_cache[key] = (token, time.time() + data.get("token_ttl", 3600))
    return token


async def call_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    # Read per call: on a tenant call these come from the connector only.
    base_url = str(tenant_getenv("LOOKER_BASE_URL", "") or "").rstrip("/")
    client_id = str(tenant_getenv("LOOKER_CLIENT_ID", "") or "")
    client_secret = str(tenant_getenv("LOOKER_CLIENT_SECRET", "") or "")
    if not base_url or not client_id or not client_secret:
        return {"error": "Looker base URL, client ID and client secret are not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            token = await _get_token(client, base_url, client_id, client_secret)
            headers = {"Authorization": f"token {token}"}

            if tool_name == "looker_run_look":
                resp = await client.get(
                    f"{base_url}/api/4.0/looks/{params['look_id']}/run/{params.get('result_format', 'json')}",  # noqa: E501
                    params={"limit": params.get("limit", 500)},
                    headers=headers,
                )
                resp.raise_for_status()
                return {"result": resp.json()}

            if tool_name == "looker_list_dashboards":
                resp = await client.get(
                    f"{base_url}/api/4.0/dashboards",
                    params={
                        "fields": params.get("fields", "id,title,description"),
                        "limit": params.get("limit", 20),
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                return {"dashboards": resp.json()}

            if tool_name == "looker_query_model":
                body = {
                    "model": params["model"],
                    "view": params["explore"],
                    "fields": params.get("dimensions", []) + params.get("measures", []),
                    "filters": params.get("filters", {}),
                    "limit": str(params.get("limit", 500)),
                }
                resp = await client.post(
                    f"{base_url}/api/4.0/queries/run/json",
                    json=body,
                    headers=headers,
                )
                resp.raise_for_status()
                return {"result": resp.json()}

            if tool_name == "looker_list_explores":
                resp = await client.get(
                    f"{base_url}/api/4.0/lookml_models/{params['model']}/explores",
                    headers=headers,
                )
                resp.raise_for_status()
                return {"explores": resp.json()}

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as e:
            logger.warning("looker.http_error", status=e.response.status_code, tool=tool_name)
            return {"error": f"Looker API error {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.error("looker.tool_error", tool=tool_name, error=str(e))
            return {"error": str(e)}
