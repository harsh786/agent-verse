"""Google Forms MCP server — form creation and response management via Google Forms API.

Environment:
  GOOGLE_ACCESS_TOKEN: OAuth2 bearer token with forms.body and forms.responses.readonly scopes
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

FORMS_BASE = "https://forms.googleapis.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "gforms_list_forms",
        "description": "List Google Forms in Drive (uses Drive API as Forms API has no list endpoint)",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "gforms_create_form",
        "description": "Create a new Google Form",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "document_title": {
                    "type": "string",
                    "description": "Document title shown in Drive",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "gforms_get_form_responses",
        "description": "Get responses submitted to a Google Form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
                "page_size": {"type": "integer", "default": 100},
                "filter": {
                    "type": "string",
                    "description": "RFC 3339 timestamp filter e.g. timestamp > 2024-01-01T00:00:00Z",
                },
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "gforms_update_form",
        "description": "Update a Google Form's title or description via batchUpdate",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "gforms_delete_form",
        "description": "Delete a Google Form from Drive",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "gforms_add_question",
        "description": "Add a question to an existing Google Form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string"},
                "question_title": {"type": "string"},
                "question_type": {
                    "type": "string",
                    "enum": [
                        "SHORT_ANSWER",
                        "PARAGRAPH",
                        "MULTIPLE_CHOICE",
                        "CHECKBOXES",
                        "DROPDOWN",
                        "DATE",
                        "TIME",
                    ],
                    "default": "SHORT_ANSWER",
                },
                "choices": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Required for MULTIPLE_CHOICE, CHECKBOXES, DROPDOWN",
                },
                "required": {"type": "boolean", "default": False},
            },
            "required": ["form_id", "question_title"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    if not token:
        return {"error": "GOOGLE_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "gforms_list_forms":
                # Use Drive API to list forms (mimeType filter)
                r = await c.get(
                    "https://www.googleapis.com/drive/v3/files",
                    headers=hdrs,
                    params={
                        "q": "mimeType='application/vnd.google-apps.form'",
                        "pageSize": arguments.get("max_results", 20),
                        "fields": "files(id,name,createdTime,modifiedTime)",
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "forms": [
                        {
                            "id": f.get("id"),
                            "name": f.get("name"),
                            "created_time": f.get("createdTime"),
                            "modified_time": f.get("modifiedTime"),
                        }
                        for f in data.get("files", [])
                    ]
                }

            elif tool_name == "gforms_create_form":
                body: dict[str, Any] = {
                    "info": {
                        "title": arguments["title"],
                        "documentTitle": arguments.get(
                            "document_title", arguments["title"]
                        ),
                    }
                }
                r = await c.post(f"{FORMS_BASE}/forms", headers=hdrs, json=body)
                r.raise_for_status()
                data = r.json()
                return {
                    "form_id": data.get("formId"),
                    "title": data.get("info", {}).get("title"),
                    "responder_uri": data.get("responderUri"),
                    "created": True,
                }

            elif tool_name == "gforms_get_form_responses":
                params: dict[str, Any] = {"pageSize": arguments.get("page_size", 100)}
                if arguments.get("filter"):
                    params["filter"] = arguments["filter"]
                r = await c.get(
                    f"{FORMS_BASE}/forms/{arguments['form_id']}/responses",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "responses": data.get("responses", []),
                    "total_responses": len(data.get("responses", [])),
                }

            elif tool_name == "gforms_update_form":
                requests: list[dict[str, Any]] = []
                if arguments.get("title") or arguments.get("description"):
                    update_info: dict[str, Any] = {}
                    mask_paths = []
                    if arguments.get("title"):
                        update_info["title"] = arguments["title"]
                        mask_paths.append("title")
                    if arguments.get("description"):
                        update_info["description"] = arguments["description"]
                        mask_paths.append("description")
                    requests.append(
                        {
                            "updateFormInfo": {
                                "info": update_info,
                                "updateMask": ",".join(mask_paths),
                            }
                        }
                    )
                if not requests:
                    return {"error": "At least one of title or description is required"}
                r = await c.post(
                    f"{FORMS_BASE}/forms/{arguments['form_id']}:batchUpdate",
                    headers=hdrs,
                    json={"requests": requests},
                )
                r.raise_for_status()
                return {"form_id": arguments["form_id"], "updated": True}

            elif tool_name == "gforms_delete_form":
                r = await c.delete(
                    f"https://www.googleapis.com/drive/v3/files/{arguments['form_id']}",
                    headers=hdrs,
                )
                r.raise_for_status()
                return {"form_id": arguments["form_id"], "deleted": True}

            elif tool_name == "gforms_add_question":
                q_type = arguments.get("question_type", "SHORT_ANSWER")
                question: dict[str, Any] = {
                    "title": arguments["question_title"],
                    "required": arguments.get("required", False),
                }
                if q_type == "SHORT_ANSWER":
                    question["textQuestion"] = {"paragraph": False}
                elif q_type == "PARAGRAPH":
                    question["textQuestion"] = {"paragraph": True}
                elif q_type in ("MULTIPLE_CHOICE", "CHECKBOXES", "DROPDOWN"):
                    options = [{"value": ch} for ch in arguments.get("choices", [])]
                    question["choiceQuestion"] = {
                        "type": q_type,
                        "options": options,
                    }
                elif q_type == "DATE":
                    question["dateQuestion"] = {}
                elif q_type == "TIME":
                    question["timeQuestion"] = {}
                r = await c.post(
                    f"{FORMS_BASE}/forms/{arguments['form_id']}:batchUpdate",
                    headers=hdrs,
                    json={
                        "requests": [
                            {
                                "createItem": {
                                    "item": {"title": arguments["question_title"], "questionItem": {"question": question}},
                                    "location": {"index": 0},
                                }
                            }
                        ]
                    },
                )
                r.raise_for_status()
                return {
                    "form_id": arguments["form_id"],
                    "question_title": arguments["question_title"],
                    "added": True,
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("gforms_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
