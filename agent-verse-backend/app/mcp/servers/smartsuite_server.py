"""SmartSuite MCP server — SmartSuite REST API v1 integration.

Environment variables:
  SMARTSUITE_API_KEY: SmartSuite API key
  SMARTSUITE_ACCOUNT_ID: SmartSuite account slug/ID
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

SMARTSUITE_BASE = "https://app.smartsuite.com/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "smartsuite_list_solutions",
        "description": "List all SmartSuite solutions (apps/workspaces) in the account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "smartsuite_list_tables",
        "description": "List tables (data tables) within a SmartSuite solution",
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "string", "description": "Solution ID"},
            },
            "required": ["solution_id"],
        },
    },
    {
        "name": "smartsuite_list_records",
        "description": "List records in a SmartSuite table with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "table_id": {"type": "string"},
                "filter": {
                    "type": "object",
                    "description": "SmartSuite filter object",
                },
                "sort": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Field slugs to include in response",
                },
                "page_size": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["table_id"],
        },
    },
    {
        "name": "smartsuite_get_record",
        "description": "Get a single SmartSuite record by its ID",
        "parameters": {
            "type": "object",
            "properties": {
                "table_id": {"type": "string"},
                "record_id": {"type": "string"},
            },
            "required": ["table_id", "record_id"],
        },
    },
    {
        "name": "smartsuite_create_record",
        "description": "Create a new record in a SmartSuite table",
        "parameters": {
            "type": "object",
            "properties": {
                "table_id": {"type": "string"},
                "fields": {
                    "type": "object",
                    "description": "Record fields as key-value pairs (field slug: value)",
                },
            },
            "required": ["table_id", "fields"],
        },
    },
    {
        "name": "smartsuite_update_record",
        "description": "Update an existing SmartSuite record",
        "parameters": {
            "type": "object",
            "properties": {
                "table_id": {"type": "string"},
                "record_id": {"type": "string"},
                "fields": {
                    "type": "object",
                    "description": "Fields to update as key-value pairs",
                },
            },
            "required": ["table_id", "record_id", "fields"],
        },
    },
    {
        "name": "smartsuite_delete_record",
        "description": "Delete a SmartSuite record by its ID",
        "parameters": {
            "type": "object",
            "properties": {
                "table_id": {"type": "string"},
                "record_id": {"type": "string"},
            },
            "required": ["table_id", "record_id"],
        },
    },
    {
        "name": "smartsuite_add_comment",
        "description": "Add a comment to a SmartSuite record",
        "parameters": {
            "type": "object",
            "properties": {
                "table_id": {"type": "string"},
                "record_id": {"type": "string"},
                "comment": {"type": "string"},
            },
            "required": ["table_id", "record_id", "comment"],
        },
    },
]


def _smartsuite_headers() -> dict[str, str]:
    api_key = os.getenv("SMARTSUITE_API_KEY", "")
    account_id = os.getenv("SMARTSUITE_ACCOUNT_ID", "")
    return {
        "Authorization": f"Token {api_key}",
        "ACCOUNT-ID": account_id,
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        return await _call_tool_inner(tool_name, arguments)
    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.text[:500]
        except Exception:
            pass
        return {"error": f"HTTP {exc.response.status_code}: {error_body or exc.response.reason_phrase}", "status_code": exc.response.status_code}
    except Exception as exc:
        logger.error("call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}


async def _call_tool_inner(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("SMARTSUITE_API_KEY", "")
    account_id = os.getenv("SMARTSUITE_ACCOUNT_ID", "")
    if not api_key or not account_id:
        return {"error": "SMARTSUITE_API_KEY and SMARTSUITE_ACCOUNT_ID must be configured"}

    async with httpx.AsyncClient(
        base_url=SMARTSUITE_BASE, headers=_smartsuite_headers(), timeout=30.0
    ) as client:
        if tool_name == "smartsuite_list_solutions":
            resp = await client.get("/solution/")
            resp.raise_for_status()
            data = resp.json()
            solutions = data if isinstance(data, list) else data.get("results", [])
            return {
                "solutions": [
                    {
                        "id": s.get("id", ""),
                        "name": s.get("name", ""),
                        "slug": s.get("slug", ""),
                        "created_on": s.get("created_on", ""),
                    }
                    for s in solutions
                ]
            }

        elif tool_name == "smartsuite_list_tables":
            solution_id = arguments["solution_id"]
            resp = await client.get(f"/solution/{solution_id}/table/")
            resp.raise_for_status()
            data = resp.json()
            tables = data if isinstance(data, list) else data.get("results", [])
            return {
                "tables": [
                    {
                        "id": t.get("id", ""),
                        "name": t.get("name", ""),
                        "slug": t.get("slug", ""),
                    }
                    for t in tables
                ]
            }

        elif tool_name == "smartsuite_list_records":
            table_id = arguments["table_id"]
            payload: dict[str, Any] = {
                "limit": arguments.get("page_size", 50),
                "offset": arguments.get("offset", 0),
            }
            if arguments.get("filter"):
                payload["filter"] = arguments["filter"]
            if arguments.get("sort"):
                payload["sort"] = arguments["sort"]
            if arguments.get("fields"):
                payload["fields"] = arguments["fields"]

            resp = await client.post(f"/applications/{table_id}/records/list/", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "total": data.get("total_count", 0),
                "records": data.get("items", []),
            }

        elif tool_name == "smartsuite_get_record":
            table_id = arguments["table_id"]
            record_id = arguments["record_id"]
            resp = await client.get(f"/applications/{table_id}/records/{record_id}/")
            resp.raise_for_status()
            return {"record": resp.json()}

        elif tool_name == "smartsuite_create_record":
            table_id = arguments["table_id"]
            resp = await client.post(
                f"/applications/{table_id}/records/",
                json=arguments["fields"],
            )
            resp.raise_for_status()
            data = resp.json()
            return {"record_id": data.get("id", ""), "created": True}

        elif tool_name == "smartsuite_update_record":
            table_id = arguments["table_id"]
            record_id = arguments["record_id"]
            resp = await client.patch(
                f"/applications/{table_id}/records/{record_id}/",
                json=arguments["fields"],
            )
            resp.raise_for_status()
            return {"record_id": record_id, "updated": True}

        elif tool_name == "smartsuite_delete_record":
            table_id = arguments["table_id"]
            record_id = arguments["record_id"]
            resp = await client.delete(f"/applications/{table_id}/records/{record_id}/")
            resp.raise_for_status()
            return {"record_id": record_id, "deleted": True}

        elif tool_name == "smartsuite_add_comment":
            table_id = arguments["table_id"]
            record_id = arguments["record_id"]
            payload = {
                "application": table_id,
                "record": record_id,
                "comment": arguments["comment"],
            }
            resp = await client.post(f"/applications/{table_id}/records/{record_id}/comments/", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"comment_id": data.get("id", ""), "created": True}

        else:
            return {"error": f"Unknown tool: {tool_name}"}
