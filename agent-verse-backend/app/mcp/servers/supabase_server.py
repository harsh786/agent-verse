"""Supabase MCP server — interact with Supabase via REST API and Auth Admin.

Environment:
  SUPABASE_URL:         Project URL (e.g. https://xyz.supabase.co)
  SUPABASE_SERVICE_KEY: Service role key (has full access, keep secret)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "supabase_select",
        "description": "Select rows from a Supabase table with optional filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Table name"},
                "select": {"type": "string", "default": "*", "description": "Columns to return"},
                "filters": {
                    "type": "object",
                    "description": "Key-value filters (e.g. {id: 'eq.1', status: 'eq.active'})",
                    "default": {},
                },
                "limit": {"type": "integer", "default": 100},
                "order": {"type": "string", "description": "e.g. created_at.desc"},
            },
            "required": ["table"],
        },
    },
    {
        "name": "supabase_insert",
        "description": "Insert one or more rows into a Supabase table",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "data": {
                    "type": "object",
                    "description": "Row data as object, or array of objects for bulk insert",
                },
            },
            "required": ["table", "data"],
        },
    },
    {
        "name": "supabase_update",
        "description": "Update rows in a Supabase table matching filters",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "data": {"type": "object", "description": "Fields to update"},
                "filters": {
                    "type": "object",
                    "description": "Key-value filters (e.g. {id: 'eq.1'})",
                },
            },
            "required": ["table", "data", "filters"],
        },
    },
    {
        "name": "supabase_delete",
        "description": "Delete rows from a Supabase table matching filters",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "filters": {
                    "type": "object",
                    "description": "Key-value filters (e.g. {id: 'eq.1'})",
                },
            },
            "required": ["table", "filters"],
        },
    },
    {
        "name": "supabase_execute_sql",
        "description": "Call a Supabase RPC (database function)",
        "parameters": {
            "type": "object",
            "properties": {
                "function_name": {"type": "string", "description": "PostgreSQL function name"},
                "params": {"type": "object", "default": {}, "description": "Function parameters"},
            },
            "required": ["function_name"],
        },
    },
    {
        "name": "supabase_list_tables",
        "description": "List all user tables in the Supabase database (public schema)",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "supabase_auth_list_users",
        "description": "List users from Supabase Auth (requires service key)",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 50},
            },
        },
    },
]


def _headers(service_key: str) -> dict[str, str]:
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_KEY", "")

    if not url:
        return {"error": "SUPABASE_URL not configured"}
    if not service_key:
        return {"error": "SUPABASE_SERVICE_KEY not configured"}

    rest_base = f"{url}/rest/v1"
    headers = _headers(service_key)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if tool_name == "supabase_select":
                table = arguments["table"]
                params: dict[str, Any] = {"select": arguments.get("select", "*")}
                for k, v in (arguments.get("filters") or {}).items():
                    params[k] = v
                if limit := arguments.get("limit", 100):
                    params["limit"] = limit
                if order := arguments.get("order"):
                    params["order"] = order
                resp = await client.get(f"{rest_base}/{table}", params=params, headers=headers)
                resp.raise_for_status()
                return {"data": resp.json(), "count": len(resp.json())}

            elif tool_name == "supabase_insert":
                table = arguments["table"]
                resp = await client.post(
                    f"{rest_base}/{table}", json=arguments["data"], headers=headers
                )
                resp.raise_for_status()
                return {"data": resp.json()}

            elif tool_name == "supabase_update":
                table = arguments["table"]
                params = {}
                for k, v in (arguments.get("filters") or {}).items():
                    params[k] = v
                resp = await client.patch(
                    f"{rest_base}/{table}",
                    json=arguments["data"],
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                return {"data": resp.json()}

            elif tool_name == "supabase_delete":
                table = arguments["table"]
                params = {}
                for k, v in (arguments.get("filters") or {}).items():
                    params[k] = v
                resp = await client.delete(
                    f"{rest_base}/{table}", params=params, headers=headers
                )
                resp.raise_for_status()
                data = resp.json() if resp.content else []
                return {"data": data}

            elif tool_name == "supabase_execute_sql":
                fn = arguments["function_name"]
                params = arguments.get("params") or {}
                resp = await client.post(f"{rest_base}/rpc/{fn}", json=params, headers=headers)
                resp.raise_for_status()
                return {"result": resp.json()}

            elif tool_name == "supabase_list_tables":
                # Use the pg_tables RPC or information_schema via RPC
                resp = await client.post(
                    f"{rest_base}/rpc/get_tables",
                    json={},
                    headers=headers,
                )
                if resp.status_code == 404:
                    # Fallback: query information_schema via select
                    resp2 = await client.get(
                        f"{rest_base}/information_schema_tables",
                        params={"select": "table_name", "table_schema": "eq.public"},
                        headers=headers,
                    )
                    if resp2.status_code == 200:
                        return {"tables": [r["table_name"] for r in resp2.json()]}
                    return {"tables": [], "note": "Requires get_tables RPC function or pg_tables access"}
                resp.raise_for_status()
                return {"tables": resp.json()}

            elif tool_name == "supabase_auth_list_users":
                page = arguments.get("page", 1)
                per_page = arguments.get("per_page", 50)
                resp = await client.get(
                    f"{url}/auth/v1/admin/users",
                    params={"page": page, "per_page": per_page},
                    headers=headers,
                )
                resp.raise_for_status()
                return resp.json()

            else:
                return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("supabase_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
