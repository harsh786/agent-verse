"""Ninox MCP server — database management, tables, and records.

Environment:
  NINOX_API_KEY: Ninox API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.ninox.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "ninox_list_databases",
        "description": "List all databases in Ninox",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "ninox_list_tables",
        "description": "List all tables in a Ninox database",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Ninox team ID"},
                "database_id": {"type": "string"},
            },
            "required": ["team_id", "database_id"],
        },
    },
    {
        "name": "ninox_query_records",
        "description": "Query records from a Ninox table",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "database_id": {"type": "string"},
                "table_id": {"type": "string"},
                "filters": {"type": "object", "description": "Filter conditions as key-value pairs"},
                "per_page": {"type": "integer", "default": 100},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["team_id", "database_id", "table_id"],
        },
    },
    {
        "name": "ninox_create_record",
        "description": "Create a new record in a Ninox table",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "database_id": {"type": "string"},
                "table_id": {"type": "string"},
                "fields": {"type": "object", "description": "Field values for the new record"},
            },
            "required": ["team_id", "database_id", "table_id", "fields"],
        },
    },
    {
        "name": "ninox_update_record",
        "description": "Update an existing record in a Ninox table",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "database_id": {"type": "string"},
                "table_id": {"type": "string"},
                "record_id": {"type": "string"},
                "fields": {"type": "object"},
            },
            "required": ["team_id", "database_id", "table_id", "record_id", "fields"],
        },
    },
    {
        "name": "ninox_delete_record",
        "description": "Delete a record from a Ninox table",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "database_id": {"type": "string"},
                "table_id": {"type": "string"},
                "record_id": {"type": "string"},
            },
            "required": ["team_id", "database_id", "table_id", "record_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("NINOX_API_KEY", "")
    if not api_key:
        return {"error": "NINOX_API_KEY not configured"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "ninox_list_databases":
                r = await c.get(f"{BASE}/teams")
                r.raise_for_status()
                return r.json()

            elif tool_name == "ninox_list_tables":
                tid = arguments["team_id"]
                dbid = arguments["database_id"]
                r = await c.get(f"{BASE}/teams/{tid}/databases/{dbid}/tables")
                r.raise_for_status()
                return r.json()

            elif tool_name == "ninox_query_records":
                tid = arguments["team_id"]
                dbid = arguments["database_id"]
                tblid = arguments["table_id"]
                params: dict[str, Any] = {
                    "perPage": arguments.get("per_page", 100),
                    "page": arguments.get("page", 1),
                }
                if filters := arguments.get("filters"):
                    params.update(filters)
                r = await c.get(f"{BASE}/teams/{tid}/databases/{dbid}/tables/{tblid}/records", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "ninox_create_record":
                tid = arguments["team_id"]
                dbid = arguments["database_id"]
                tblid = arguments["table_id"]
                r = await c.post(
                    f"{BASE}/teams/{tid}/databases/{dbid}/tables/{tblid}/records",
                    json=[arguments["fields"]],
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "ninox_update_record":
                tid = arguments["team_id"]
                dbid = arguments["database_id"]
                tblid = arguments["table_id"]
                rid = arguments["record_id"]
                r = await c.put(
                    f"{BASE}/teams/{tid}/databases/{dbid}/tables/{tblid}/records/{rid}",
                    json=arguments["fields"],
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "ninox_delete_record":
                tid = arguments["team_id"]
                dbid = arguments["database_id"]
                tblid = arguments["table_id"]
                rid = arguments["record_id"]
                r = await c.delete(f"{BASE}/teams/{tid}/databases/{dbid}/tables/{tblid}/records/{rid}")
                r.raise_for_status()
                return {"status": "deleted"} if r.status_code in (200, 204) else r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("ninox_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
