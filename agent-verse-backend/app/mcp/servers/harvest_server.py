"""Harvest MCP server — time tracking, expense management, projects, and invoicing.

Environment:
  HARVEST_ACCESS_TOKEN: Harvest personal access token
  HARVEST_ACCOUNT_ID:   Harvest account ID
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.harvestapp.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "harvest_list_time_entries",
        "description": "List time entries, optionally filtered by project, user, or date range",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "user_id": {"type": "integer"},
                "from_date": {"type": "string", "description": "YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "YYYY-MM-DD"},
                "is_billed": {"type": "boolean"},
                "per_page": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "harvest_create_time_entry",
        "description": "Create a new time entry",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "task_id": {"type": "integer"},
                "spent_date": {"type": "string", "description": "YYYY-MM-DD"},
                "hours": {"type": "number"},
                "notes": {"type": "string"},
                "user_id": {"type": "integer"},
            },
            "required": ["project_id", "task_id", "spent_date"],
        },
    },
    {
        "name": "harvest_list_projects",
        "description": "List all projects in the Harvest account",
        "parameters": {
            "type": "object",
            "properties": {
                "is_active": {"type": "boolean", "default": True},
                "client_id": {"type": "integer"},
                "per_page": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "harvest_list_clients",
        "description": "List all clients in the Harvest account",
        "parameters": {
            "type": "object",
            "properties": {
                "is_active": {"type": "boolean", "default": True},
                "per_page": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "harvest_list_invoices",
        "description": "List invoices, optionally filtered by client or status",
        "parameters": {
            "type": "object",
            "properties": {
                "client_id": {"type": "integer"},
                "state": {
                    "type": "string",
                    "enum": ["draft", "open", "paid", "closed"],
                },
                "from_date": {"type": "string", "description": "YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "YYYY-MM-DD"},
                "per_page": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "harvest_get_project_budget",
        "description": "Get budget information for a specific project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
            },
            "required": ["project_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("HARVEST_ACCESS_TOKEN", "")
    account_id = os.getenv("HARVEST_ACCOUNT_ID", "")
    if not token or not account_id:
        return {"error": "HARVEST_ACCESS_TOKEN and HARVEST_ACCOUNT_ID must be configured"}

    headers = {
        "Authorization": f"Bearer {token}",
        "Harvest-Account-Id": account_id,
        "User-Agent": "AgentVerse MCP (agentverse@example.com)",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "harvest_list_time_entries":
                params: dict[str, Any] = {"per_page": arguments.get("per_page", 100)}
                for key in ("project_id", "user_id", "from", "to", "is_billed"):
                    mapped = {"from_date": "from", "to_date": "to"}.get(key, key)
                    val = arguments.get(mapped if mapped != key else key)
                    if val is None:
                        val = arguments.get(key)
                    if val is not None:
                        params[key] = val
                if fd := arguments.get("from_date"):
                    params["from"] = fd
                if td := arguments.get("to_date"):
                    params["to"] = td
                r = await c.get(f"{BASE}/time_entries", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "harvest_create_time_entry":
                payload: dict[str, Any] = {
                    "project_id": arguments["project_id"],
                    "task_id": arguments["task_id"],
                    "spent_date": arguments["spent_date"],
                }
                if hours := arguments.get("hours"):
                    payload["hours"] = hours
                if notes := arguments.get("notes"):
                    payload["notes"] = notes
                if uid := arguments.get("user_id"):
                    payload["user_id"] = uid
                r = await c.post(f"{BASE}/time_entries", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "harvest_list_projects":
                params = {"per_page": arguments.get("per_page", 100)}
                if ia := arguments.get("is_active"):
                    params["is_active"] = str(ia).lower()
                if cid := arguments.get("client_id"):
                    params["client_id"] = cid
                r = await c.get(f"{BASE}/projects", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "harvest_list_clients":
                params = {"per_page": arguments.get("per_page", 100)}
                if ia := arguments.get("is_active"):
                    params["is_active"] = str(ia).lower()
                r = await c.get(f"{BASE}/clients", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "harvest_list_invoices":
                params = {"per_page": arguments.get("per_page", 100)}
                if cid := arguments.get("client_id"):
                    params["client_id"] = cid
                if state := arguments.get("state"):
                    params["state"] = state
                if fd := arguments.get("from_date"):
                    params["from"] = fd
                if td := arguments.get("to_date"):
                    params["to"] = td
                r = await c.get(f"{BASE}/invoices", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "harvest_get_project_budget":
                pid = arguments["project_id"]
                r = await c.get(f"{BASE}/projects/{pid}")
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "budget": data.get("budget"),
                    "budget_by": data.get("budget_by"),
                    "budget_is_monthly": data.get("budget_is_monthly"),
                    "over_budget_notification_percentage": data.get("over_budget_notification_percentage"),
                    "cost_budget": data.get("cost_budget"),
                    "fee": data.get("fee"),
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("harvest_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
