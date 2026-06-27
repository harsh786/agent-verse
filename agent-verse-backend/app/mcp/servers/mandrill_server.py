"""Mandrill (Mailchimp Transactional) MCP server.

Environment:
  MANDRILL_API_KEY: Mandrill API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

MANDRILL_BASE = "https://mandrillapp.com/api/1.0"

TOOL_DEFINITIONS = [
    {
        "name": "mandrill_send_email",
        "description": "Send a transactional email via Mandrill",
        "parameters": {
            "type": "object",
            "properties": {
                "to_email": {"type": "string"},
                "to_name": {"type": "string", "default": ""},
                "subject": {"type": "string"},
                "html": {"type": "string", "description": "HTML body"},
                "text": {"type": "string", "description": "Plain text body"},
                "from_email": {"type": "string"},
                "from_name": {"type": "string", "default": ""},
                "important": {"type": "boolean", "default": False},
                "track_opens": {"type": "boolean", "default": True},
                "track_clicks": {"type": "boolean", "default": True},
            },
            "required": ["to_email", "subject"],
        },
    },
    {
        "name": "mandrill_send_template",
        "description": "Send an email using a Mandrill/Mailchimp template",
        "parameters": {
            "type": "object",
            "properties": {
                "to_email": {"type": "string"},
                "template_name": {"type": "string"},
                "template_content": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    },
                    "description": "Template variable values",
                },
                "subject": {"type": "string"},
                "from_email": {"type": "string"},
            },
            "required": ["to_email", "template_name"],
        },
    },
    {
        "name": "mandrill_list_templates",
        "description": "List all available Mandrill templates",
        "parameters": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Filter by label"},
            },
        },
    },
    {
        "name": "mandrill_get_message_info",
        "description": "Get info for a sent Mandrill message by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "mandrill_list_senders",
        "description": "List sender domains and stats",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "mandrill_search_messages",
        "description": "Search sent message history",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                "limit": {"type": "integer", "default": 25},
            },
            "required": ["query"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("MANDRILL_API_KEY", "")
    if not api_key:
        return {"error": "MANDRILL_API_KEY not configured"}

    default_from = os.getenv("MANDRILL_FROM_EMAIL", "noreply@example.com")

    try:
        async with httpx.AsyncClient(
            base_url=MANDRILL_BASE, timeout=30.0
        ) as c:
            if tool_name == "mandrill_send_email":
                message: dict[str, Any] = {
                    "to": [{"email": arguments["to_email"], "name": arguments.get("to_name", "")}],
                    "subject": arguments["subject"],
                    "from_email": arguments.get("from_email", default_from),
                    "from_name": arguments.get("from_name", ""),
                    "important": arguments.get("important", False),
                    "track_opens": arguments.get("track_opens", True),
                    "track_clicks": arguments.get("track_clicks", True),
                }
                if arguments.get("html"):
                    message["html"] = arguments["html"]
                if arguments.get("text"):
                    message["text"] = arguments["text"]
                r = await c.post(
                    "/messages/send.json",
                    json={"key": api_key, "message": message},
                )
                r.raise_for_status()
                results = r.json()
                if isinstance(results, list) and results:
                    return {
                        "_id": results[0].get("_id"),
                        "status": results[0].get("status"),
                        "email": results[0].get("email"),
                    }
                return {"results": results}

            elif tool_name == "mandrill_send_template":
                message = {
                    "to": [{"email": arguments["to_email"]}],
                    "from_email": arguments.get("from_email", default_from),
                }
                if "subject" in arguments:
                    message["subject"] = arguments["subject"]
                r = await c.post(
                    "/messages/send-template.json",
                    json={
                        "key": api_key,
                        "template_name": arguments["template_name"],
                        "template_content": arguments.get("template_content", []),
                        "message": message,
                    },
                )
                r.raise_for_status()
                results = r.json()
                if isinstance(results, list) and results:
                    return {"_id": results[0].get("_id"), "status": results[0].get("status")}
                return {"results": results}

            elif tool_name == "mandrill_list_templates":
                payload: dict[str, Any] = {"key": api_key}
                if "label" in arguments:
                    payload["label"] = arguments["label"]
                r = await c.post("/templates/list.json", json=payload)
                r.raise_for_status()
                return {
                    "templates": [
                        {"slug": t.get("slug"), "name": t.get("name"), "labels": t.get("labels", [])}
                        for t in r.json()
                    ]
                }

            elif tool_name == "mandrill_get_message_info":
                r = await c.post(
                    "/messages/info.json",
                    json={"key": api_key, "id": arguments["message_id"]},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "_id": data.get("_id"),
                    "state": data.get("state"),
                    "subject": data.get("subject"),
                    "email": data.get("email"),
                    "opens": data.get("opens", 0),
                    "clicks": data.get("clicks", 0),
                }

            elif tool_name == "mandrill_list_senders":
                r = await c.post("/senders/list.json", json={"key": api_key})
                r.raise_for_status()
                return {"senders": r.json()}

            elif tool_name == "mandrill_search_messages":
                payload = {
                    "key": api_key,
                    "query": arguments["query"],
                    "limit": arguments.get("limit", 25),
                }
                if "date_from" in arguments:
                    payload["date_from"] = arguments["date_from"]
                if "date_to" in arguments:
                    payload["date_to"] = arguments["date_to"]
                r = await c.post("/messages/search.json", json=payload)
                r.raise_for_status()
                return {
                    "messages": [
                        {
                            "_id": m.get("_id"),
                            "subject": m.get("subject"),
                            "email": m.get("email"),
                            "state": m.get("state"),
                        }
                        for m in r.json()
                    ]
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("mandrill_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
