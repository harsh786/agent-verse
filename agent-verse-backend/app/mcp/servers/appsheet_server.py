"""AppSheet MCP server — no-code app data management and action invocation.

Environment:
  APPSHEET_APP_ID: AppSheet application ID
  APPSHEET_ACCESS_KEY: AppSheet API access key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base_url() -> str:
    app_id = os.getenv("APPSHEET_APP_ID", "")
    return f"https://api.appsheet.com/api/v2/apps/{app_id}"


TOOL_DEFINITIONS = [
    {
        "name": "appsheet_list_apps",
        "description": "List AppSheet applications accessible with the credentials",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "appsheet_get_app_data",
        "description": "Get rows from a table in an AppSheet application",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "Name of the AppSheet table"},
                "selector": {"type": "string", "description": "AppSheet expression to filter rows"},
                "rows": {
                    "type": "array",
                    "description": "Optional list of specific row keys to retrieve",
                    "items": {"type": "object"},
                },
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "appsheet_add_record",
        "description": "Add one or more new rows to an AppSheet table",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "Name of the AppSheet table"},
                "rows": {
                    "type": "array",
                    "description": "Array of row objects to insert",
                    "items": {"type": "object"},
                },
            },
            "required": ["table_name", "rows"],
        },
    },
    {
        "name": "appsheet_update_record",
        "description": "Update existing rows in an AppSheet table",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "Table name"},
                "rows": {
                    "type": "array",
                    "description": "Rows to update with key fields and new values",
                    "items": {"type": "object"},
                },
            },
            "required": ["table_name", "rows"],
        },
    },
    {
        "name": "appsheet_delete_record",
        "description": "Delete rows from an AppSheet table by key",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "Table name"},
                "rows": {
                    "type": "array",
                    "description": "Row key objects to delete",
                    "items": {"type": "object"},
                },
            },
            "required": ["table_name", "rows"],
        },
    },
    {
        "name": "appsheet_invoke_action",
        "description": "Invoke a defined action on rows in an AppSheet application",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "Table containing the action"},
                "action": {"type": "string", "description": "Name of the action to invoke"},
                "rows": {
                    "type": "array",
                    "description": "Row key objects to run the action on",
                    "items": {"type": "object"},
                },
            },
            "required": ["table_name", "action"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    app_id = os.getenv("APPSHEET_APP_ID", "")
    access_key = os.getenv("APPSHEET_ACCESS_KEY", "")
    if not app_id or not access_key:
        return {"error": "APPSHEET_APP_ID and APPSHEET_ACCESS_KEY not configured"}

    base_url = _base_url()
    headers = {
        "ApplicationAccessKey": access_key,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "appsheet_list_apps":
                r = await client.get(
                    "https://api.appsheet.com/api/v2/apps",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "appsheet_get_app_data":
                payload: dict[str, Any] = {
                    "Action": "Find",
                    "Properties": {},
                }
                if "selector" in arguments:
                    payload["Properties"]["Selector"] = arguments["selector"]
                if "rows" in arguments:
                    payload["Rows"] = arguments["rows"]
                r = await client.post(
                    f"{base_url}/tables/{arguments['table_name']}/Action",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "appsheet_add_record":
                r = await client.post(
                    f"{base_url}/tables/{arguments['table_name']}/Action",
                    headers=headers,
                    json={"Action": "Add", "Rows": arguments["rows"]},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "appsheet_update_record":
                r = await client.post(
                    f"{base_url}/tables/{arguments['table_name']}/Action",
                    headers=headers,
                    json={"Action": "Edit", "Rows": arguments["rows"]},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "appsheet_delete_record":
                r = await client.post(
                    f"{base_url}/tables/{arguments['table_name']}/Action",
                    headers=headers,
                    json={"Action": "Delete", "Rows": arguments["rows"]},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "appsheet_invoke_action":
                payload = {
                    "Action": arguments["action"],
                    "Properties": {"Locale": "en-US"},
                }
                if "rows" in arguments:
                    payload["Rows"] = arguments["rows"]
                r = await client.post(
                    f"{base_url}/tables/{arguments['table_name']}/Action",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
