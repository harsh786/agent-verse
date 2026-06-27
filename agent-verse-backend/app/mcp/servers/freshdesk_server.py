"""Freshdesk MCP server — customer support tickets and contacts.

Environment:
  FRESHDESK_DOMAIN:  Freshdesk subdomain (e.g. 'mycompany')
  FRESHDESK_API_KEY: API key (used as HTTP Basic username, password='X')
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base() -> str:
    domain = os.getenv("FRESHDESK_DOMAIN", "")
    return f"https://{domain}.freshdesk.com/api/v2"


def _headers() -> dict[str, str]:
    api_key = os.getenv("FRESHDESK_API_KEY", "")
    creds = base64.b64encode(f"{api_key}:X".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "freshdesk_list_tickets",
        "description": "List Freshdesk support tickets",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 30},
                "order_by": {"type": "string", "default": "created_at"},
                "order_type": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
                "filter": {
                    "type": "string",
                    "description": "Predefined filter: new_and_my_open, watching, spam, deleted",
                },
            },
        },
    },
    {
        "name": "freshdesk_get_ticket",
        "description": "Get a single Freshdesk ticket by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "freshdesk_create_ticket",
        "description": "Create a new Freshdesk support ticket",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "email": {"type": "string", "description": "Requester email"},
                "priority": {
                    "type": "integer",
                    "description": "1=Low, 2=Medium, 3=High, 4=Urgent",
                    "default": 2,
                },
                "status": {
                    "type": "integer",
                    "description": "2=Open, 3=Pending, 4=Resolved, 5=Closed",
                    "default": 2,
                },
                "type": {"type": "string", "description": "Ticket type, e.g. 'Question'"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "group_id": {"type": "integer"},
                "responder_id": {"type": "integer", "description": "Agent ID to assign"},
            },
            "required": ["subject", "email"],
        },
    },
    {
        "name": "freshdesk_update_ticket",
        "description": "Update an existing Freshdesk ticket",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "status": {"type": "integer"},
                "priority": {"type": "integer"},
                "group_id": {"type": "integer"},
                "responder_id": {"type": "integer"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "subject": {"type": "string"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "freshdesk_reply_ticket",
        "description": "Add a reply to a Freshdesk ticket",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "body": {"type": "string", "description": "HTML reply body"},
                "user_id": {"type": "integer", "description": "Agent sending the reply"},
                "cc_emails": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "CC email addresses",
                },
            },
            "required": ["ticket_id", "body"],
        },
    },
    {
        "name": "freshdesk_search_tickets",
        "description": "Search Freshdesk tickets using query syntax",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query, e.g. 'status:2 AND priority:3'",
                },
                "page": {"type": "integer", "default": 1},
            },
            "required": ["query"],
        },
    },
    {
        "name": "freshdesk_list_contacts",
        "description": "List Freshdesk contacts",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 30},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    domain = os.getenv("FRESHDESK_DOMAIN", "")
    api_key = os.getenv("FRESHDESK_API_KEY", "")
    if not domain or not api_key:
        return {"error": "FRESHDESK_DOMAIN and FRESHDESK_API_KEY must be configured"}

    base = _base()

    try:
        async with httpx.AsyncClient(headers=_headers(), timeout=30.0) as c:
            if tool_name == "freshdesk_list_tickets":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 30),
                    "order_by": arguments.get("order_by", "created_at"),
                    "order_type": arguments.get("order_type", "desc"),
                }
                if f := arguments.get("filter"):
                    params["filter"] = f
                r = await c.get(f"{base}/tickets", params=params)
                r.raise_for_status()
                return {"tickets": r.json()}

            elif tool_name == "freshdesk_get_ticket":
                r = await c.get(f"{base}/tickets/{arguments['ticket_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshdesk_create_ticket":
                payload: dict[str, Any] = {
                    "subject": arguments["subject"],
                    "email": arguments["email"],
                    "priority": arguments.get("priority", 2),
                    "status": arguments.get("status", 2),
                }
                if desc := arguments.get("description"):
                    payload["description"] = desc
                if ttype := arguments.get("type"):
                    payload["type"] = ttype
                if tags := arguments.get("tags"):
                    payload["tags"] = tags
                for field in ["group_id", "responder_id"]:
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.post(f"{base}/tickets", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshdesk_update_ticket":
                tid = arguments["ticket_id"]
                payload = {}
                for field in ["status", "priority", "group_id", "responder_id", "tags", "subject"]:
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await c.put(f"{base}/tickets/{tid}", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshdesk_reply_ticket":
                tid = arguments["ticket_id"]
                payload = {"body": arguments["body"]}
                if user_id := arguments.get("user_id"):
                    payload["user_id"] = user_id
                if cc := arguments.get("cc_emails"):
                    payload["cc_emails"] = cc
                r = await c.post(f"{base}/tickets/{tid}/reply", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshdesk_search_tickets":
                r = await c.get(
                    f"{base}/search/tickets",
                    params={"query": f'"{arguments["query"]}"', "page": arguments.get("page", 1)},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshdesk_list_contacts":
                r = await c.get(
                    f"{base}/contacts",
                    params={
                        "page": arguments.get("page", 1),
                        "per_page": arguments.get("per_page", 30),
                    },
                )
                r.raise_for_status()
                return {"contacts": r.json()}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("freshdesk_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
