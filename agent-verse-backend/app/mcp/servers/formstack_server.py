"""Formstack MCP server — forms, submissions, and documents.

Environment:
  FORMSTACK_API_KEY: Formstack API access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

FORMSTACK_BASE = "https://www.formstack.com/api/v2"

TOOL_DEFINITIONS = [
    {
        "name": "formstack_list_forms",
        "description": "List all forms in the Formstack account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
                "folder_id": {"type": "string", "description": "Filter by folder"},
            },
        },
    },
    {
        "name": "formstack_get_submissions",
        "description": "Get submissions for a Formstack form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
                "min_time": {"type": "string", "description": "ISO8601 start date filter"},
                "max_time": {"type": "string", "description": "ISO8601 end date filter"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "formstack_create_form",
        "description": "Create a new Formstack form",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "submissions_unread": {"type": "integer", "default": 0},
            },
            "required": ["name"],
        },
    },
    {
        "name": "formstack_list_fields",
        "description": "List all fields in a Formstack form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "formstack_get_form_analytics",
        "description": "Get analytics (views, submissions, conversion rate) for a form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
                "data_type": {
                    "type": "string",
                    "enum": ["views", "submissions", "conversions"],
                    "default": "submissions",
                },
                "min_date": {"type": "string"},
                "max_date": {"type": "string"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "formstack_send_form",
        "description": "Send a Formstack form via email",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
                "recipients": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Recipient email addresses",
                },
                "message": {"type": "string", "description": "Optional email message"},
            },
            "required": ["form_id", "recipients"],
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("FORMSTACK_API_KEY", "")
    if not api_key:
        return {"error": "FORMSTACK_API_KEY not configured"}

    hdrs = _headers(api_key)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "formstack_list_forms":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                }
                if arguments.get("folder_id"):
                    params["folder_id"] = arguments["folder_id"]
                r = await c.get(f"{FORMSTACK_BASE}/form.json", headers=hdrs, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "forms": [
                        {
                            "id": f.get("id"),
                            "name": f.get("name"),
                            "submissions": f.get("submissions"),
                            "views": f.get("views"),
                            "url": f.get("url"),
                        }
                        for f in data.get("forms", [])
                    ],
                    "total": data.get("total", 0),
                }

            elif tool_name == "formstack_get_submissions":
                params = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                }
                if arguments.get("min_time"):
                    params["min_time"] = arguments["min_time"]
                if arguments.get("max_time"):
                    params["max_time"] = arguments["max_time"]
                r = await c.get(
                    f"{FORMSTACK_BASE}/form/{arguments['form_id']}/submission.json",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "submissions": data.get("submissions", []),
                    "total": data.get("total", 0),
                }

            elif tool_name == "formstack_create_form":
                r = await c.post(
                    f"{FORMSTACK_BASE}/form.json",
                    headers=hdrs,
                    json={"name": arguments["name"]},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "url": data.get("url"),
                    "created": True,
                }

            elif tool_name == "formstack_list_fields":
                r = await c.get(
                    f"{FORMSTACK_BASE}/form/{arguments['form_id']}/field.json",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "fields": [
                        {
                            "id": fld.get("id"),
                            "label": fld.get("label"),
                            "type": fld.get("type"),
                            "required": fld.get("required"),
                        }
                        for fld in data
                    ]
                    if isinstance(data, list)
                    else data
                }

            elif tool_name == "formstack_get_form_analytics":
                params = {
                    "data": arguments.get("data_type", "submissions"),
                }
                if arguments.get("min_date"):
                    params["min_date"] = arguments["min_date"]
                if arguments.get("max_date"):
                    params["max_date"] = arguments["max_date"]
                r = await c.get(
                    f"{FORMSTACK_BASE}/form/{arguments['form_id']}/analytics.json",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                return {"analytics": r.json()}

            elif tool_name == "formstack_send_form":
                r = await c.post(
                    f"{FORMSTACK_BASE}/form/{arguments['form_id']}/send.json",
                    headers=hdrs,
                    json={
                        "recipients": arguments["recipients"],
                        "message": arguments.get("message", ""),
                    },
                )
                r.raise_for_status()
                return {"sent": True, "form_id": arguments["form_id"]}

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("formstack_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
