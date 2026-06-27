"""Google Sheets MCP server — read/write spreadsheet data via Sheets API v4.

Environment variables (one required):
  GOOGLE_ACCESS_TOKEN:         OAuth2 bearer token
  GOOGLE_SERVICE_ACCOUNT_JSON: JSON string of a service-account key file
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

SHEETS_BASE = "https://sheets.googleapis.com/v4"

TOOL_DEFINITIONS = [
    {
        "name": "sheets_read_range",
        "description": "Read values from a range in a Google Sheet",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string", "description": "Spreadsheet ID from the URL"},
                "range": {"type": "string", "description": "A1 notation range, e.g. Sheet1!A1:D10"},
                "major_dimension": {
                    "type": "string",
                    "enum": ["ROWS", "COLUMNS"],
                    "default": "ROWS",
                },
            },
            "required": ["spreadsheet_id", "range"],
        },
    },
    {
        "name": "sheets_write_range",
        "description": "Write values to a range in a Google Sheet (overwrites existing data)",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range": {"type": "string", "description": "A1 notation range"},
                "values": {
                    "type": "array",
                    "items": {"type": "array"},
                    "description": "2-D array of values to write",
                },
                "major_dimension": {"type": "string", "enum": ["ROWS", "COLUMNS"], "default": "ROWS"},
            },
            "required": ["spreadsheet_id", "range", "values"],
        },
    },
    {
        "name": "sheets_append_rows",
        "description": "Append rows after the last row with data in a sheet",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range": {"type": "string", "default": "Sheet1!A1", "description": "Sheet range hint"},
                "values": {"type": "array", "items": {"type": "array"}},
            },
            "required": ["spreadsheet_id", "values"],
        },
    },
    {
        "name": "sheets_clear_range",
        "description": "Clear all values from a range in a Google Sheet",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range": {"type": "string"},
            },
            "required": ["spreadsheet_id", "range"],
        },
    },
    {
        "name": "sheets_list_sheets",
        "description": "List all sheet tabs (names and IDs) within a spreadsheet",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
            },
            "required": ["spreadsheet_id"],
        },
    },
    {
        "name": "sheets_create_spreadsheet",
        "description": "Create a new Google Spreadsheet",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title of the new spreadsheet"},
                "sheet_titles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of sheet tab titles to create",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "sheets_batch_update",
        "description": "Apply one or more batchUpdate requests to a spreadsheet (formatting, merges, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "requests": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of Sheets API Request objects",
                },
            },
            "required": ["spreadsheet_id", "requests"],
        },
    },
    {
        "name": "sheets_get_metadata",
        "description": "Get full spreadsheet metadata including all sheet properties and named ranges",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
            },
            "required": ["spreadsheet_id"],
        },
    },
]


def _google_token() -> str:
    """Obtain a Google access token from env."""
    direct = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    if direct:
        return direct
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if sa_json:
        try:
            from google.auth.transport.requests import Request  # type: ignore[import]
            from google.oauth2 import service_account  # type: ignore[import]

            creds = service_account.Credentials.from_service_account_info(
                json.loads(sa_json),
                scopes=[
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ],
            )
            creds.refresh(Request())
            return creds.token  # type: ignore[return-value]
        except Exception:
            logger.debug("google_service_account_refresh_failed", exc_info=True)
    return ""


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = _google_token()
    if not token:
        return {"error": "GOOGLE_ACCESS_TOKEN or GOOGLE_SERVICE_ACCOUNT_JSON required"}

    hdrs = _headers(token)
    sid = arguments.get("spreadsheet_id", "")

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "sheets_read_range":
                rng = arguments["range"]
                params: dict[str, Any] = {
                    "majorDimension": arguments.get("major_dimension", "ROWS")
                }
                r = await c.get(
                    f"{SHEETS_BASE}/spreadsheets/{sid}/values/{rng}",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "sheets_write_range":
                rng = arguments["range"]
                body = {
                    "range": rng,
                    "majorDimension": arguments.get("major_dimension", "ROWS"),
                    "values": arguments["values"],
                }
                r = await c.put(
                    f"{SHEETS_BASE}/spreadsheets/{sid}/values/{rng}",
                    headers=hdrs,
                    params={"valueInputOption": "USER_ENTERED"},
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "sheets_append_rows":
                rng = arguments.get("range", "Sheet1!A1")
                body = {
                    "majorDimension": "ROWS",
                    "values": arguments["values"],
                }
                r = await c.post(
                    f"{SHEETS_BASE}/spreadsheets/{sid}/values/{rng}:append",
                    headers=hdrs,
                    params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "sheets_clear_range":
                rng = arguments["range"]
                r = await c.post(
                    f"{SHEETS_BASE}/spreadsheets/{sid}/values/{rng}:clear",
                    headers=hdrs,
                    json={},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "sheets_list_sheets":
                r = await c.get(
                    f"{SHEETS_BASE}/spreadsheets/{sid}",
                    headers=hdrs,
                    params={"fields": "sheets.properties"},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "sheets": [
                        {
                            "sheet_id": s["properties"]["sheetId"],
                            "title": s["properties"]["title"],
                            "index": s["properties"]["index"],
                            "sheet_type": s["properties"].get("sheetType", "GRID"),
                        }
                        for s in data.get("sheets", [])
                    ]
                }

            elif tool_name == "sheets_create_spreadsheet":
                body: dict[str, Any] = {"properties": {"title": arguments["title"]}}
                if sheet_titles := arguments.get("sheet_titles"):
                    body["sheets"] = [
                        {"properties": {"title": t}} for t in sheet_titles
                    ]
                r = await c.post(
                    f"{SHEETS_BASE}/spreadsheets",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "spreadsheet_id": data["spreadsheetId"],
                    "spreadsheet_url": data.get("spreadsheetUrl", ""),
                    "title": data["properties"]["title"],
                }

            elif tool_name == "sheets_batch_update":
                body = {"requests": arguments["requests"]}
                r = await c.post(
                    f"{SHEETS_BASE}/spreadsheets/{sid}:batchUpdate",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "sheets_get_metadata":
                r = await c.get(
                    f"{SHEETS_BASE}/spreadsheets/{sid}",
                    headers=hdrs,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("sheets_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
