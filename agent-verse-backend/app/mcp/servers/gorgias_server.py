"""Gorgias MCP server — e-commerce customer support platform.

Environment:
  GORGIAS_DOMAIN:  Gorgias subdomain (e.g. 'mystore')
  GORGIAS_EMAIL:   Account email
  GORGIAS_API_KEY: API key
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base() -> str:
    domain = os.getenv("GORGIAS_DOMAIN", "")
    return f"https://{domain}.gorgias.com/api"


def _headers() -> dict[str, str]:
    email = os.getenv("GORGIAS_EMAIL", "")
    api_key = os.getenv("GORGIAS_API_KEY", "")
    creds = base64.b64encode(f"{email}:{api_key}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "gorgias_list_tickets",
        "description": "List Gorgias support tickets",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 30},
                "cursor": {"type": "string", "description": "Pagination cursor"},
                "status": {"type": "string", "enum": ["open", "closed"]},
                "channel": {"type": "string"},
            },
        },
    },
    {
        "name": "gorgias_get_ticket",
        "description": "Get a Gorgias ticket by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "gorgias_create_ticket",
        "description": "Create a new Gorgias customer support ticket",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "enum": ["email", "chat", "sms", "instagram", "facebook"],
                    "default": "email",
                },
                "via": {"type": "string", "description": "Source channel"},
                "from_agent": {"type": "boolean", "default": False},
                "customer_email": {"type": "string"},
                "customer_name": {"type": "string"},
                "subject": {"type": "string"},
                "body_text": {"type": "string"},
            },
            "required": ["customer_email", "subject", "body_text"],
        },
    },
    {
        "name": "gorgias_update_ticket",
        "description": "Update a Gorgias ticket status or assignment",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["open", "closed"]},
                "assignee_user_id": {"type": "integer"},
                "assignee_team_id": {"type": "integer"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "gorgias_add_message",
        "description": "Add a message/reply to a Gorgias ticket",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "body_text": {"type": "string"},
                "body_html": {"type": "string"},
                "from_agent": {"type": "boolean", "default": True},
                "channel": {"type": "string", "default": "email"},
            },
            "required": ["ticket_id", "body_text"],
        },
    },
    {
        "name": "gorgias_list_customers",
        "description": "List or search Gorgias customers",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Filter by email"},
                "limit": {"type": "integer", "default": 30},
                "cursor": {"type": "string"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    domain = os.getenv("GORGIAS_DOMAIN", "")
    email = os.getenv("GORGIAS_EMAIL", "")
    api_key = os.getenv("GORGIAS_API_KEY", "")
    if not all([domain, email, api_key]):
        return {"error": "GORGIAS_DOMAIN, GORGIAS_EMAIL, and GORGIAS_API_KEY must be configured"}

    base = _base()

    try:
        async with httpx.AsyncClient(headers=_headers(), timeout=30.0) as c:
            if tool_name == "gorgias_list_tickets":
                params: dict[str, Any] = {"limit": arguments.get("limit", 30)}
                if cursor := arguments.get("cursor"):
                    params["cursor"] = cursor
                if status := arguments.get("status"):
                    params["status"] = status
                if channel := arguments.get("channel"):
                    params["channel"] = channel
                r = await c.get(f"{base}/tickets", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "gorgias_get_ticket":
                r = await c.get(f"{base}/tickets/{arguments['ticket_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "gorgias_create_ticket":
                ticket: dict[str, Any] = {
                    "channel": arguments.get("channel", "email"),
                    "from_agent": arguments.get("from_agent", False),
                    "customer": {
                        "email": arguments["customer_email"],
                        "name": arguments.get("customer_name", arguments["customer_email"]),
                    },
                    "messages": [
                        {
                            "channel": arguments.get("channel", "email"),
                            "from_agent": arguments.get("from_agent", False),
                            "subject": arguments["subject"],
                            "body_text": arguments["body_text"],
                        }
                    ],
                }
                r = await c.post(f"{base}/tickets", json=ticket)
                r.raise_for_status()
                return r.json()

            elif tool_name == "gorgias_update_ticket":
                tid = arguments["ticket_id"]
                payload: dict[str, Any] = {}
                if status := arguments.get("status"):
                    payload["status"] = status
                if uid := arguments.get("assignee_user_id"):
                    payload["assignee_user"] = {"id": uid}
                if team_id := arguments.get("assignee_team_id"):
                    payload["assignee_team"] = {"id": team_id}
                r = await c.put(f"{base}/tickets/{tid}", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "gorgias_add_message":
                tid = arguments["ticket_id"]
                msg: dict[str, Any] = {
                    "channel": arguments.get("channel", "email"),
                    "from_agent": arguments.get("from_agent", True),
                    "body_text": arguments["body_text"],
                }
                if html := arguments.get("body_html"):
                    msg["body_html"] = html
                r = await c.post(f"{base}/tickets/{tid}/messages", json=msg)
                r.raise_for_status()
                return r.json()

            elif tool_name == "gorgias_list_customers":
                params = {"limit": arguments.get("limit", 30)}
                if email_filter := arguments.get("email"):
                    params["email"] = email_filter
                if cursor := arguments.get("cursor"):
                    params["cursor"] = cursor
                r = await c.get(f"{base}/customers", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("gorgias_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
