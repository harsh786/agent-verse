"""Zendesk MCP server — tickets, users, organizations, and search.

Environment:
  ZENDESK_SUBDOMAIN:  Zendesk subdomain (e.g. 'mycompany')
  ZENDESK_EMAIL:      Agent email address
  ZENDESK_API_TOKEN:  API token from Admin > Apps & Integrations > API
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base() -> str:
    subdomain = os.getenv("ZENDESK_SUBDOMAIN", "")
    return f"https://{subdomain}.zendesk.com/api/v2"


def _headers() -> dict[str, str]:
    email = os.getenv("ZENDESK_EMAIL", "")
    token = os.getenv("ZENDESK_API_TOKEN", "")
    creds = base64.b64encode(f"{email}/token:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "zendesk_list_tickets",
        "description": "List Zendesk support tickets",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
                "sort_by": {"type": "string", "default": "created_at"},
                "sort_order": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
            },
        },
    },
    {
        "name": "zendesk_get_ticket",
        "description": "Get a single Zendesk ticket by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "zendesk_create_ticket",
        "description": "Create a new Zendesk support ticket",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Ticket description"},
                "requester_email": {"type": "string"},
                "requester_name": {"type": "string"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "urgent"],
                    "default": "normal",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "type": {"type": "string", "enum": ["problem", "incident", "question", "task"]},
            },
            "required": ["subject", "body"],
        },
    },
    {
        "name": "zendesk_update_ticket",
        "description": "Update an existing Zendesk ticket",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "status": {
                    "type": "string",
                    "enum": ["new", "open", "pending", "hold", "solved", "closed"],
                },
                "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                "assignee_id": {"type": "integer"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "subject": {"type": "string"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "zendesk_add_comment",
        "description": "Add a public or private comment to a Zendesk ticket",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "body": {"type": "string"},
                "public": {"type": "boolean", "default": True},
            },
            "required": ["ticket_id", "body"],
        },
    },
    {
        "name": "zendesk_search",
        "description": "Search across all Zendesk objects (tickets, users, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Zendesk search query, e.g. 'type:ticket status:open'",
                },
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
            },
            "required": ["query"],
        },
    },
    {
        "name": "zendesk_list_users",
        "description": "List Zendesk users (agents and end-users)",
        "parameters": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "enum": ["end-user", "agent", "admin"],
                    "description": "Filter by role",
                },
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
            },
        },
    },
    {
        "name": "zendesk_list_organizations",
        "description": "List Zendesk organizations",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    subdomain = os.getenv("ZENDESK_SUBDOMAIN", "")
    email = os.getenv("ZENDESK_EMAIL", "")
    token = os.getenv("ZENDESK_API_TOKEN", "")
    if not all([subdomain, email, token]):
        return {"error": "ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, and ZENDESK_API_TOKEN must be configured"}

    base = _base()

    try:
        async with httpx.AsyncClient(headers=_headers(), timeout=30.0) as c:
            if tool_name == "zendesk_list_tickets":
                r = await c.get(
                    f"{base}/tickets.json",
                    params={
                        "page": arguments.get("page", 1),
                        "per_page": arguments.get("per_page", 25),
                        "sort_by": arguments.get("sort_by", "created_at"),
                        "sort_order": arguments.get("sort_order", "desc"),
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "zendesk_get_ticket":
                tid = arguments["ticket_id"]
                r = await c.get(f"{base}/tickets/{tid}.json")
                r.raise_for_status()
                return r.json()

            elif tool_name == "zendesk_create_ticket":
                ticket: dict[str, Any] = {
                    "subject": arguments["subject"],
                    "comment": {"body": arguments["body"]},
                    "priority": arguments.get("priority", "normal"),
                }
                if email := arguments.get("requester_email"):
                    ticket["requester"] = {
                        "email": email,
                        "name": arguments.get("requester_name", email),
                    }
                if tags := arguments.get("tags"):
                    ticket["tags"] = tags
                if ttype := arguments.get("type"):
                    ticket["type"] = ttype
                r = await c.post(f"{base}/tickets.json", json={"ticket": ticket})
                r.raise_for_status()
                return r.json()

            elif tool_name == "zendesk_update_ticket":
                tid = arguments["ticket_id"]
                ticket = {}
                for field in ["status", "priority", "assignee_id", "tags", "subject"]:
                    if field in arguments:
                        ticket[field] = arguments[field]
                r = await c.put(f"{base}/tickets/{tid}.json", json={"ticket": ticket})
                r.raise_for_status()
                return r.json()

            elif tool_name == "zendesk_add_comment":
                tid = arguments["ticket_id"]
                ticket = {
                    "comment": {
                        "body": arguments["body"],
                        "public": arguments.get("public", True),
                    }
                }
                r = await c.put(f"{base}/tickets/{tid}.json", json={"ticket": ticket})
                r.raise_for_status()
                return r.json()

            elif tool_name == "zendesk_search":
                r = await c.get(
                    f"{base}/search.json",
                    params={
                        "query": arguments["query"],
                        "page": arguments.get("page", 1),
                        "per_page": arguments.get("per_page", 25),
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "zendesk_list_users":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                }
                if role := arguments.get("role"):
                    params["role"] = role
                r = await c.get(f"{base}/users.json", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zendesk_list_organizations":
                r = await c.get(
                    f"{base}/organizations.json",
                    params={
                        "page": arguments.get("page", 1),
                        "per_page": arguments.get("per_page", 25),
                    },
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("zendesk_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
