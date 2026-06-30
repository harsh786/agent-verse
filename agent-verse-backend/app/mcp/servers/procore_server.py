"""Procore MCP server — construction management, projects, RFIs, submittals, and budgets.

Environment:
  PROCORE_ACCESS_TOKEN: Procore OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.procore.com/rest/v1.0"

TOOL_DEFINITIONS = [
    {
        "name": "procore_list_projects",
        "description": "List all Procore projects",
        "parameters": {
            "type": "object",
            "properties": {
                "company_id": {"type": "integer", "description": "Procore company ID"},
                "status": {"type": "string", "enum": ["Active", "Inactive"], "default": "Active"},
                "per_page": {"type": "integer", "default": 30},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["company_id"],
        },
    },
    {
        "name": "procore_list_rfi",
        "description": "List RFIs (Requests for Information) for a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["open", "closed", "draft"]},
                "per_page": {"type": "integer", "default": 30},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "procore_create_rfi",
        "description": "Create a new RFI for a Procore project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "subject": {"type": "string"},
                "question": {"type": "string"},
                "assignee_id": {"type": "integer"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["project_id", "subject"],
        },
    },
    {
        "name": "procore_list_submittals",
        "description": "List submittals for a Procore project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "status": {"type": "string"},
                "per_page": {"type": "integer", "default": 30},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "procore_list_meetings",
        "description": "List meetings for a Procore project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "per_page": {"type": "integer", "default": 30},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "procore_get_project_budget",
        "description": "Get the budget line items for a Procore project",
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
    access_token = os.getenv("PROCORE_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "PROCORE_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "procore_list_projects":
                cid = arguments["company_id"]
                params: dict[str, Any] = {
                    "company_id": cid,
                    "per_page": arguments.get("per_page", 30),
                    "page": arguments.get("page", 1),
                }
                if status := arguments.get("status"):
                    params["filters[status]"] = status
                r = await c.get(f"{BASE}/projects", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "procore_list_rfi":
                pid = arguments["project_id"]
                params = {
                    "project_id": pid,
                    "per_page": arguments.get("per_page", 30),
                    "page": arguments.get("page", 1),
                }
                if status := arguments.get("status"):
                    params["filters[status]"] = status
                r = await c.get(f"{BASE}/projects/{pid}/rfis", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "procore_create_rfi":
                pid = arguments["project_id"]
                rfi: dict[str, Any] = {
                    "subject": arguments["subject"],
                    "project_id": pid,
                }
                for field in ("question", "assignee_id", "due_date"):
                    if v := arguments.get(field):
                        rfi[field] = v
                r = await c.post(f"{BASE}/projects/{pid}/rfis", json={"rfi": rfi})
                r.raise_for_status()
                return r.json()

            elif tool_name == "procore_list_submittals":
                pid = arguments["project_id"]
                params = {
                    "project_id": pid,
                    "per_page": arguments.get("per_page", 30),
                    "page": arguments.get("page", 1),
                }
                if status := arguments.get("status"):
                    params["filters[status]"] = status
                r = await c.get(f"{BASE}/projects/{pid}/submittals", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "procore_list_meetings":
                pid = arguments["project_id"]
                params = {
                    "project_id": pid,
                    "per_page": arguments.get("per_page", 30),
                    "page": arguments.get("page", 1),
                }
                r = await c.get(f"{BASE}/projects/{pid}/meetings", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "procore_get_project_budget":
                pid = arguments["project_id"]
                r = await c.get(f"{BASE}/projects/{pid}/budget_line_items", params={"project_id": pid})
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("procore_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
