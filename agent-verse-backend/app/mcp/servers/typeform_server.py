"""Typeform MCP server — form building and response collection.

Environment:
  TYPEFORM_ACCESS_TOKEN: Typeform personal access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TYPEFORM_BASE = "https://api.typeform.com"

TOOL_DEFINITIONS = [
    {
        "name": "typeform_list_forms",
        "description": "List all forms in the Typeform account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "page_size": {"type": "integer", "default": 20},
                "search": {"type": "string", "description": "Filter by title"},
            },
        },
    },
    {
        "name": "typeform_get_form",
        "description": "Get the full definition of a Typeform form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "typeform_list_responses",
        "description": "Get responses submitted to a Typeform form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
                "page_size": {"type": "integer", "default": 25},
                "since": {"type": "string", "description": "ISO8601 start date filter"},
                "until": {"type": "string", "description": "ISO8601 end date filter"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "typeform_create_form",
        "description": "Create a new Typeform form",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "fields": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of field definitions",
                },
                "settings": {"type": "object", "description": "Form settings"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "typeform_delete_form",
        "description": "Delete a Typeform form permanently",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "typeform_get_insights",
        "description": "Get completion rate and drop-off insights for a form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
            },
            "required": ["form_id"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("TYPEFORM_ACCESS_TOKEN", "")
    if not token:
        return {"error": "TYPEFORM_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "typeform_list_forms":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "page_size": arguments.get("page_size", 20),
                }
                if arguments.get("search"):
                    params["search"] = arguments["search"]
                r = await c.get(f"{TYPEFORM_BASE}/forms", headers=hdrs, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "forms": [
                        {
                            "id": f.get("id"),
                            "title": f.get("title"),
                            "last_updated_at": f.get("last_updated_at"),
                            "response_count": f.get("_links", {}).get("responses"),
                        }
                        for f in data.get("items", [])
                    ],
                    "total_items": data.get("total_items", 0),
                }

            elif tool_name == "typeform_get_form":
                r = await c.get(
                    f"{TYPEFORM_BASE}/forms/{arguments['form_id']}", headers=hdrs
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "fields": data.get("fields", []),
                    "settings": data.get("settings", {}),
                    "logic": data.get("logic", []),
                }

            elif tool_name == "typeform_list_responses":
                params = {"page_size": arguments.get("page_size", 25)}
                if arguments.get("since"):
                    params["since"] = arguments["since"]
                if arguments.get("until"):
                    params["until"] = arguments["until"]
                r = await c.get(
                    f"{TYPEFORM_BASE}/forms/{arguments['form_id']}/responses",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "responses": data.get("items", []),
                    "total_items": data.get("total_items", 0),
                    "page_count": data.get("page_count", 0),
                }

            elif tool_name == "typeform_create_form":
                body: dict[str, Any] = {"title": arguments["title"]}
                if arguments.get("fields"):
                    body["fields"] = arguments["fields"]
                if arguments.get("settings"):
                    body["settings"] = arguments["settings"]
                r = await c.post(f"{TYPEFORM_BASE}/forms", headers=hdrs, json=body)
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "created": True,
                    "link": data.get("_links", {}).get("display"),
                }

            elif tool_name == "typeform_delete_form":
                r = await c.delete(
                    f"{TYPEFORM_BASE}/forms/{arguments['form_id']}", headers=hdrs
                )
                r.raise_for_status()
                return {"deleted": True, "form_id": arguments["form_id"]}

            elif tool_name == "typeform_get_insights":
                r = await c.get(
                    f"{TYPEFORM_BASE}/forms/{arguments['form_id']}/insights/summary",
                    headers=hdrs,
                )
                r.raise_for_status()
                return r.json()

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("typeform_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
