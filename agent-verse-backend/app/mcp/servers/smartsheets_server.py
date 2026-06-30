"""Smartsheet MCP server — project management sheets, rows, and reports.

Environment:
  SMARTSHEET_ACCESS_TOKEN: Smartsheet API access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.smartsheet.com/2.0"

TOOL_DEFINITIONS = [
    {
        "name": "smartsheet_list_sheets",
        "description": "List all Smartsheet sheets accessible to the user",
        "parameters": {
            "type": "object",
            "properties": {
                "include_all": {"type": "boolean", "default": False},
                "page_size": {"type": "integer", "default": 100},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "smartsheet_get_sheet",
        "description": "Get a specific Smartsheet sheet with all rows and columns",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id": {"type": "integer"},
                "page_size": {"type": "integer", "default": 100},
            },
            "required": ["sheet_id"],
        },
    },
    {
        "name": "smartsheet_create_row",
        "description": "Add a new row to a Smartsheet sheet",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id": {"type": "integer"},
                "cells": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column_id": {"type": "integer"},
                            "value": {},
                        },
                    },
                    "description": "Cell values keyed by column ID",
                },
                "to_top": {"type": "boolean", "default": False},
            },
            "required": ["sheet_id", "cells"],
        },
    },
    {
        "name": "smartsheet_update_row",
        "description": "Update cells in an existing Smartsheet row",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id": {"type": "integer"},
                "row_id": {"type": "integer"},
                "cells": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column_id": {"type": "integer"},
                            "value": {},
                        },
                    },
                },
            },
            "required": ["sheet_id", "row_id", "cells"],
        },
    },
    {
        "name": "smartsheet_list_reports",
        "description": "List all reports in the Smartsheet account",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "default": 100},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "smartsheet_share_sheet",
        "description": "Share a sheet with a user or group",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id": {"type": "integer"},
                "email": {"type": "string"},
                "access_level": {
                    "type": "string",
                    "enum": ["VIEWER", "COMMENTER", "EDITOR", "EDITOR_SHARE", "ADMIN", "OWNER"],
                    "default": "VIEWER",
                },
                "subject": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["sheet_id", "email"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("SMARTSHEET_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "SMARTSHEET_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "smartsheet_list_sheets":
                params: dict[str, Any] = {
                    "pageSize": arguments.get("page_size", 100),
                    "page": arguments.get("page", 1),
                }
                if arguments.get("include_all"):
                    params["includeAll"] = True
                r = await c.get(f"{BASE}/sheets", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "smartsheet_get_sheet":
                sid = arguments["sheet_id"]
                params = {"pageSize": arguments.get("page_size", 100)}
                r = await c.get(f"{BASE}/sheets/{sid}", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "smartsheet_create_row":
                sid = arguments["sheet_id"]
                row: dict[str, Any] = {"cells": arguments["cells"]}
                if arguments.get("to_top"):
                    row["toTop"] = True
                r = await c.post(f"{BASE}/sheets/{sid}/rows", json=[row])
                r.raise_for_status()
                return r.json()

            elif tool_name == "smartsheet_update_row":
                sid = arguments["sheet_id"]
                row = {
                    "id": arguments["row_id"],
                    "cells": arguments["cells"],
                }
                r = await c.put(f"{BASE}/sheets/{sid}/rows", json=[row])
                r.raise_for_status()
                return r.json()

            elif tool_name == "smartsheet_list_reports":
                params = {
                    "pageSize": arguments.get("page_size", 100),
                    "page": arguments.get("page", 1),
                }
                r = await c.get(f"{BASE}/reports", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "smartsheet_share_sheet":
                sid = arguments["sheet_id"]
                share_payload: dict[str, Any] = {
                    "email": arguments["email"],
                    "accessLevel": arguments.get("access_level", "VIEWER"),
                }
                body: dict[str, Any] = {"shares": [share_payload]}
                if subj := arguments.get("subject"):
                    body["subject"] = subj
                if msg := arguments.get("message"):
                    body["message"] = msg
                r = await c.post(f"{BASE}/sheets/{sid}/shares", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("smartsheet_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
