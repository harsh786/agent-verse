"""JotForm MCP server — form building and submission management.

Environment:
  JOTFORM_API_KEY: JotForm API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

JOTFORM_BASE = "https://api.jotform.com"

TOOL_DEFINITIONS = [
    {
        "name": "jotform_list_forms",
        "description": "List all forms in the JotForm account",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
                "order_by": {
                    "type": "string",
                    "enum": ["id", "username", "title", "status", "created_at", "updated_at"],
                    "default": "created_at",
                },
            },
        },
    },
    {
        "name": "jotform_get_form",
        "description": "Get details of a specific JotForm form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "jotform_get_submissions",
        "description": "Get submissions for a JotForm form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
                "filter": {"type": "object", "description": "JSON filter object"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "jotform_create_form",
        "description": "Create a new JotForm form",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "questions": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Form question definitions",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "jotform_update_submission",
        "description": "Update an existing form submission",
        "parameters": {
            "type": "object",
            "properties": {
                "submission_id": {"type": "string"},
                "answers": {
                    "type": "object",
                    "description": "Answer data keyed by question ID",
                },
            },
            "required": ["submission_id", "answers"],
        },
    },
    {
        "name": "jotform_get_form_analytics",
        "description": "Get analytics and statistics for a JotForm form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["form_id"],
        },
    },
]


def _params(api_key: str, **extra: Any) -> dict[str, Any]:
    return {"apiKey": api_key, **extra}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("JOTFORM_API_KEY", "")
    if not api_key:
        return {"error": "JOTFORM_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "jotform_list_forms":
                r = await c.get(
                    f"{JOTFORM_BASE}/user/forms",
                    params=_params(
                        api_key,
                        limit=arguments.get("limit", 20),
                        offset=arguments.get("offset", 0),
                        orderby=arguments.get("order_by", "created_at"),
                    ),
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "forms": [
                        {
                            "id": f.get("id"),
                            "title": f.get("title"),
                            "status": f.get("status"),
                            "count": f.get("count"),
                            "created_at": f.get("created_at"),
                        }
                        for f in data.get("content", [])
                    ],
                    "result_set": data.get("resultSet", {}),
                }

            elif tool_name == "jotform_get_form":
                r = await c.get(
                    f"{JOTFORM_BASE}/form/{arguments['form_id']}",
                    params=_params(api_key),
                )
                r.raise_for_status()
                data = r.json()
                content = data.get("content", {})
                return {
                    "id": content.get("id"),
                    "title": content.get("title"),
                    "status": content.get("status"),
                    "url": content.get("url"),
                    "count": content.get("count"),
                }

            elif tool_name == "jotform_get_submissions":
                params: dict[str, Any] = _params(
                    api_key,
                    limit=arguments.get("limit", 20),
                    offset=arguments.get("offset", 0),
                )
                r = await c.get(
                    f"{JOTFORM_BASE}/form/{arguments['form_id']}/submissions",
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "submissions": data.get("content", []),
                    "result_set": data.get("resultSet", {}),
                }

            elif tool_name == "jotform_create_form":
                import json as _json

                form_data = {"questions": {}}
                if arguments.get("questions"):
                    for i, q in enumerate(arguments["questions"]):
                        form_data["questions"][str(i + 1)] = q  # type: ignore[index]
                r = await c.post(
                    f"{JOTFORM_BASE}/user/forms",
                    params=_params(api_key),
                    data={
                        "questions": _json.dumps(form_data["questions"]),
                        "properties": _json.dumps({"title": arguments["title"]}),
                    },
                )
                r.raise_for_status()
                data = r.json()
                content = data.get("content", {})
                return {
                    "id": content.get("id"),
                    "title": content.get("title"),
                    "url": content.get("url"),
                    "created": True,
                }

            elif tool_name == "jotform_update_submission":
                import json as _json

                r = await c.post(
                    f"{JOTFORM_BASE}/submission/{arguments['submission_id']}",
                    params=_params(api_key),
                    data={"submission[answers]": _json.dumps(arguments["answers"])},
                )
                r.raise_for_status()
                return {"updated": True, "submission_id": arguments["submission_id"]}

            elif tool_name == "jotform_get_form_analytics":
                params = _params(api_key)
                if arguments.get("from_date"):
                    params["from"] = arguments["from_date"]
                if arguments.get("to_date"):
                    params["to"] = arguments["to_date"]
                r = await c.get(
                    f"{JOTFORM_BASE}/form/{arguments['form_id']}/reports",
                    params=params,
                )
                r.raise_for_status()
                return {"analytics": r.json().get("content", {})}

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("jotform_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
