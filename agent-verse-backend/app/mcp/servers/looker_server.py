"""Looker MCP server — Looker Business Intelligence.

Environment:
  LOOKER_BASE_URL:  https://your-instance.looker.com
  LOOKER_CLIENT_ID: Looker API3 client ID
  LOOKER_CLIENT_SECRET: Looker API3 client secret
"""

from __future__ import annotations

import os
from typing import Any

import httpx

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

_BASE_URL = os.getenv("LOOKER_BASE_URL", "").rstrip("/")
_CLIENT_ID = os.getenv("LOOKER_CLIENT_ID", "")
_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET", "")
_token_cache: dict[str, Any] = {}


async def _get_token(client: httpx.AsyncClient) -> str:
    """Obtain a Looker API bearer token (cached)."""
    import time

    if _token_cache.get("token") and _token_cache.get("expires_at", 0) > time.time() + 60:
        return _token_cache["token"]
    resp = await client.post(
        f"{_BASE_URL}/api/4.0/login",
        data={"client_id": _CLIENT_ID, "client_secret": _CLIENT_SECRET},
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data.get("token_ttl", 3600)
    return _token_cache["token"]


async def call_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    if not _BASE_URL:
        return {"error": "LOOKER_BASE_URL not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            token = await _get_token(client)
            headers = {"Authorization": f"token {token}"}

            if tool_name == "looker_run_look":
                resp = await client.get(
                    f"{_BASE_URL}/api/4.0/looks/{params['look_id']}/run/{params.get('result_format', 'json')}",
                    params={"limit": params.get("limit", 500)},
                    headers=headers,
                )
                resp.raise_for_status()
                return {"result": resp.json()}

            if tool_name == "looker_list_dashboards":
                resp = await client.get(
                    f"{_BASE_URL}/api/4.0/dashboards",
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
                    f"{_BASE_URL}/api/4.0/queries/run/json",
                    json=body,
                    headers=headers,
                )
                resp.raise_for_status()
                return {"result": resp.json()}

            if tool_name == "looker_list_explores":
                resp = await client.get(
                    f"{_BASE_URL}/api/4.0/lookml_models/{params['model']}/explores",
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
