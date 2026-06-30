"""UpKeep MCP server — maintenance management, work orders, and asset tracking.

Environment:
  UPKEEP_API_KEY: UpKeep API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.onupkeep.com/api/v2"

TOOL_DEFINITIONS = [
    {
        "name": "upkeep_list_work_orders",
        "description": "List maintenance work orders with optional status and priority filters",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: open, pending, on hold, complete"},
                "priority": {"type": "integer", "description": "Filter by priority: 0-3 (none to critical)"},
                "limit": {"type": "integer", "description": "Maximum results"},
                "offset": {"type": "integer", "description": "Pagination offset"},
            },
        },
    },
    {
        "name": "upkeep_create_work_order",
        "description": "Create a new maintenance work order in UpKeep",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Work order title"},
                "description": {"type": "string", "description": "Detailed description of the maintenance task"},
                "priority": {"type": "integer", "description": "Priority level: 0=none, 1=low, 2=medium, 3=high, 4=critical"},
                "asset_id": {"type": "string", "description": "Asset ID to associate with the work order"},
                "location_id": {"type": "string", "description": "Location ID for the work order"},
                "due_date": {"type": "string", "description": "Due date in ISO format"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "upkeep_update_work_order",
        "description": "Update an existing work order status or details",
        "parameters": {
            "type": "object",
            "properties": {
                "work_order_id": {"type": "string", "description": "Work order ID"},
                "status": {"type": "string", "description": "New status"},
                "priority": {"type": "integer", "description": "New priority"},
                "notes": {"type": "string", "description": "Additional notes to add"},
            },
            "required": ["work_order_id"],
        },
    },
    {
        "name": "upkeep_list_assets",
        "description": "List assets tracked in UpKeep for maintenance",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Filter by asset category"},
                "limit": {"type": "integer", "description": "Maximum results"},
                "offset": {"type": "integer", "description": "Pagination offset"},
            },
        },
    },
    {
        "name": "upkeep_list_locations",
        "description": "List locations configured in UpKeep",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum locations"},
                "offset": {"type": "integer", "description": "Pagination offset"},
            },
        },
    },
    {
        "name": "upkeep_get_dashboard_stats",
        "description": "Get maintenance summary dashboard statistics",
        "parameters": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("UPKEEP_API_KEY", "")
    if not api_key:
        return {"error": "UPKEEP_API_KEY not configured"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "upkeep_list_work_orders":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/work-orders", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "upkeep_create_work_order":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(f"{BASE_URL}/work-orders", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "upkeep_update_work_order":
                woid = arguments["work_order_id"]
                payload = {k: v for k, v in arguments.items() if k != "work_order_id" and v is not None}
                r = await client.patch(f"{BASE_URL}/work-orders/{woid}", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "upkeep_list_assets":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/assets", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "upkeep_list_locations":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/locations", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "upkeep_get_dashboard_stats":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/work-orders/stats", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
