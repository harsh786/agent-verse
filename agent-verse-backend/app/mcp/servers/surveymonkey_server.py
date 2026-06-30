"""SurveyMonkey MCP server — survey creation and response analysis.

Environment:
  SURVEYMONKEY_ACCESS_TOKEN: SurveyMonkey OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

SM_BASE = "https://api.surveymonkey.com/v3"

TOOL_DEFINITIONS = [
    {
        "name": "sm_list_surveys",
        "description": "List all surveys in the SurveyMonkey account",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "default": 50},
                "page": {"type": "integer", "default": 1},
                "sort_by": {
                    "type": "string",
                    "enum": ["title", "date_modified", "num_responses"],
                    "default": "date_modified",
                },
            },
        },
    },
    {
        "name": "sm_create_survey",
        "description": "Create a new SurveyMonkey survey",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "nickname": {"type": "string"},
                "language": {"type": "string", "default": "en"},
                "pages": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Survey page definitions",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "sm_get_responses",
        "description": "Get responses submitted to a SurveyMonkey survey",
        "parameters": {
            "type": "object",
            "properties": {
                "survey_id": {"type": "string"},
                "per_page": {"type": "integer", "default": 50},
                "page": {"type": "integer", "default": 1},
                "start_created_at": {"type": "string", "description": "ISO8601 filter"},
            },
            "required": ["survey_id"],
        },
    },
    {
        "name": "sm_list_collectors",
        "description": "List collectors (distribution channels) for a survey",
        "parameters": {
            "type": "object",
            "properties": {
                "survey_id": {"type": "string"},
                "per_page": {"type": "integer", "default": 50},
            },
            "required": ["survey_id"],
        },
    },
    {
        "name": "sm_analyze_survey",
        "description": "Get summary/rollup analysis for all questions in a survey",
        "parameters": {
            "type": "object",
            "properties": {
                "survey_id": {"type": "string"},
            },
            "required": ["survey_id"],
        },
    },
    {
        "name": "sm_send_invite",
        "description": "Send an email survey invite via a SurveyMonkey email collector",
        "parameters": {
            "type": "object",
            "properties": {
                "survey_id": {"type": "string"},
                "collector_id": {"type": "string"},
                "recipients": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "email": {"type": "string"},
                            "first_name": {"type": "string"},
                            "last_name": {"type": "string"},
                        },
                    },
                },
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["survey_id", "collector_id", "recipients"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("SURVEYMONKEY_ACCESS_TOKEN", "")
    if not token:
        return {"error": "SURVEYMONKEY_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "sm_list_surveys":
                r = await c.get(
                    f"{SM_BASE}/surveys",
                    headers=hdrs,
                    params={
                        "per_page": arguments.get("per_page", 50),
                        "page": arguments.get("page", 1),
                        "sort_by": arguments.get("sort_by", "date_modified"),
                        "sort_order": "DESC",
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "surveys": [
                        {
                            "id": s.get("id"),
                            "title": s.get("title"),
                            "response_count": s.get("response_count"),
                            "date_modified": s.get("date_modified"),
                        }
                        for s in data.get("data", [])
                    ],
                    "total": data.get("total", 0),
                }

            elif tool_name == "sm_create_survey":
                body: dict[str, Any] = {
                    "title": arguments["title"],
                    "language": arguments.get("language", "en"),
                }
                if arguments.get("nickname"):
                    body["nickname"] = arguments["nickname"]
                if arguments.get("pages"):
                    body["pages"] = arguments["pages"]
                r = await c.post(f"{SM_BASE}/surveys", headers=hdrs, json=body)
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "href": data.get("href"),
                    "created": True,
                }

            elif tool_name == "sm_get_responses":
                params: dict[str, Any] = {
                    "per_page": arguments.get("per_page", 50),
                    "page": arguments.get("page", 1),
                }
                if arguments.get("start_created_at"):
                    params["start_created_at"] = arguments["start_created_at"]
                r = await c.get(
                    f"{SM_BASE}/surveys/{arguments['survey_id']}/responses/bulk",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "responses": data.get("data", []),
                    "total": data.get("total", 0),
                }

            elif tool_name == "sm_list_collectors":
                r = await c.get(
                    f"{SM_BASE}/surveys/{arguments['survey_id']}/collectors",
                    headers=hdrs,
                    params={"per_page": arguments.get("per_page", 50)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "collectors": [
                        {
                            "id": col.get("id"),
                            "name": col.get("name"),
                            "type": col.get("type"),
                            "status": col.get("status"),
                        }
                        for col in data.get("data", [])
                    ]
                }

            elif tool_name == "sm_analyze_survey":
                r = await c.get(
                    f"{SM_BASE}/surveys/{arguments['survey_id']}/rollups",
                    headers=hdrs,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "sm_send_invite":
                r = await c.post(
                    f"{SM_BASE}/collectors/{arguments['collector_id']}/messages",
                    headers=hdrs,
                    json={
                        "type": "invite",
                        "recipients": {"mail_list_ids": [], "contacts": arguments["recipients"]},
                        "subject": arguments.get("subject", "Please take our survey"),
                        "body": arguments.get("body", ""),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "message_id": data.get("id"),
                    "status": data.get("status"),
                    "sent": True,
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("sm_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
