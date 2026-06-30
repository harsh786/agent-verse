"""Microsoft Excel MCP server — workbook and spreadsheet management via Microsoft Graph API.

Environment:
  MICROSOFT_ACCESS_TOKEN: Microsoft OAuth2 access token with Files.ReadWrite scope
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

TOOL_DEFINITIONS = [
    {
        "name": "excel_list_workbooks",
        "description": "List Excel workbooks in OneDrive",
        "parameters": {
            "type": "object",
            "properties": {
                "folder_path": {"type": "string", "description": "OneDrive folder path to search"},
                "search": {"type": "string", "description": "Search term for workbook name"},
            },
        },
    },
    {
        "name": "excel_get_worksheet",
        "description": "Get a list of worksheets in an Excel workbook",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "OneDrive item ID of the workbook"},
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "excel_read_range",
        "description": "Read cell values from a range in an Excel worksheet",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "sheet_name": {"type": "string", "description": "Worksheet name"},
                "range_address": {"type": "string", "description": "A1 notation range e.g. A1:D10"},
            },
            "required": ["item_id", "sheet_name", "range_address"],
        },
    },
    {
        "name": "excel_write_range",
        "description": "Write values to a range in an Excel worksheet",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "sheet_name": {"type": "string"},
                "range_address": {"type": "string"},
                "values": {
                    "type": "array",
                    "items": {"type": "array"},
                    "description": "2D array of cell values",
                },
            },
            "required": ["item_id", "sheet_name", "range_address", "values"],
        },
    },
    {
        "name": "excel_create_table",
        "description": "Create a table from a range in an Excel worksheet",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "sheet_name": {"type": "string"},
                "range_address": {"type": "string"},
                "has_headers": {"type": "boolean", "default": True},
                "table_name": {"type": "string"},
            },
            "required": ["item_id", "sheet_name", "range_address"],
        },
    },
    {
        "name": "excel_add_chart",
        "description": "Add a chart to an Excel worksheet based on a data range",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "sheet_name": {"type": "string"},
                "chart_type": {
                    "type": "string",
                    "enum": ["ColumnClustered", "BarClustered", "Line", "Pie", "Area"],
                    "default": "ColumnClustered",
                },
                "data_range": {"type": "string", "description": "A1 notation range for chart data"},
                "title": {"type": "string"},
            },
            "required": ["item_id", "sheet_name", "data_range"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("MICROSOFT_ACCESS_TOKEN", "")
    if not token:
        return {"error": "MICROSOFT_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "excel_list_workbooks":
                if arguments.get("search"):
                    r = await c.get(
                        f"{GRAPH_BASE}/me/drive/root/search(q='{arguments['search']}')",
                        headers=hdrs,
                        params={"$filter": "file/mimeType eq 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'"},
                    )
                else:
                    folder = arguments.get("folder_path", "root")
                    r = await c.get(
                        f"{GRAPH_BASE}/me/drive/{folder}/children",
                        headers=hdrs,
                        params={"$filter": "file ne null"},
                    )
                r.raise_for_status()
                data = r.json()
                return {
                    "workbooks": [
                        {
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "last_modified": item.get("lastModifiedDateTime"),
                            "size": item.get("size"),
                        }
                        for item in data.get("value", [])
                        if item.get("name", "").endswith(".xlsx")
                    ]
                }

            elif tool_name == "excel_get_worksheet":
                item_id = arguments["item_id"]
                r = await c.get(
                    f"{GRAPH_BASE}/me/drive/items/{item_id}/workbook/worksheets",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "worksheets": [
                        {
                            "id": ws.get("id"),
                            "name": ws.get("name"),
                            "position": ws.get("position"),
                            "visibility": ws.get("visibility"),
                        }
                        for ws in data.get("value", [])
                    ]
                }

            elif tool_name == "excel_read_range":
                item_id = arguments["item_id"]
                sheet_name = arguments["sheet_name"]
                range_addr = arguments["range_address"]
                r = await c.get(
                    f"{GRAPH_BASE}/me/drive/items/{item_id}/workbook/worksheets/{sheet_name}/range(address='{range_addr}')",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "values": data.get("values", []),
                    "row_count": data.get("rowCount"),
                    "column_count": data.get("columnCount"),
                    "address": data.get("address"),
                }

            elif tool_name == "excel_write_range":
                item_id = arguments["item_id"]
                sheet_name = arguments["sheet_name"]
                range_addr = arguments["range_address"]
                r = await c.patch(
                    f"{GRAPH_BASE}/me/drive/items/{item_id}/workbook/worksheets/{sheet_name}/range(address='{range_addr}')",
                    headers=hdrs,
                    json={"values": arguments["values"]},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "address": data.get("address"),
                    "row_count": data.get("rowCount"),
                    "updated": True,
                }

            elif tool_name == "excel_create_table":
                item_id = arguments["item_id"]
                sheet_name = arguments["sheet_name"]
                body: dict[str, Any] = {
                    "address": arguments["range_address"],
                    "hasHeaders": arguments.get("has_headers", True),
                }
                r = await c.post(
                    f"{GRAPH_BASE}/me/drive/items/{item_id}/workbook/worksheets/{sheet_name}/tables/add",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "created": True,
                }

            elif tool_name == "excel_add_chart":
                item_id = arguments["item_id"]
                sheet_name = arguments["sheet_name"]
                body = {
                    "type": arguments.get("chart_type", "ColumnClustered"),
                    "sourceData": arguments["data_range"],
                    "seriesBy": "Auto",
                }
                r = await c.post(
                    f"{GRAPH_BASE}/me/drive/items/{item_id}/workbook/worksheets/{sheet_name}/charts/add",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                if arguments.get("title"):
                    await c.patch(
                        f"{GRAPH_BASE}/me/drive/items/{item_id}/workbook/worksheets/{sheet_name}/charts/{data.get('name')}/title",
                        headers=hdrs,
                        json={"text": arguments["title"]},
                    )
                return {"chart_name": data.get("name"), "created": True}

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("excel_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
