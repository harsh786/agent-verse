"""Close CRM MCP server — leads, contacts, and activities.

Environment variables:
  CLOSE_API_KEY: Close API key (used as HTTP Basic username, password empty)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

CLOSE_BASE = "https://api.close.com/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "close_list_leads",
        "description": "List Close CRM leads with optional query and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
                "_limit": {"type": "integer", "default": 25},
                "_skip": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "close_create_lead",
        "description": "Create a new lead in Close CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Lead/company name"},
                "url": {"type": "string"},
                "description": {"type": "string"},
                "contacts": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Contact objects [{name: ..., email: [{email: ...}]}]",
                },
                "custom": {"type": "object", "description": "Custom field values"},
                "status_id": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "close_update_lead",
        "description": "Update an existing Close CRM lead",
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "status_id": {"type": "string"},
                "custom": {"type": "object"},
            },
            "required": ["lead_id"],
        },
    },
    {
        "name": "close_list_contacts",
        "description": "List contacts in Close CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string", "description": "Filter by lead ID"},
                "_limit": {"type": "integer", "default": 25},
                "_skip": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "close_create_contact",
        "description": "Create a new contact linked to a lead",
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string"},
                "name": {"type": "string"},
                "title": {"type": "string"},
                "emails": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "[{email: '...', type: 'office'}]",
                },
                "phones": {
                    "type": "array",
                    "items": {"type": "object"},
                },
            },
            "required": ["lead_id", "name"],
        },
    },
    {
        "name": "close_create_activity",
        "description": "Create an activity (note, call, email, sms) on a lead",
        "parameters": {
            "type": "object",
            "properties": {
                "activity_type": {
                    "type": "string",
                    "enum": ["note", "call", "email", "sms"],
                    "default": "note",
                },
                "lead_id": {"type": "string"},
                "note": {"type": "string", "description": "Note text (for note type)"},
                "status": {"type": "string"},
                "direction": {
                    "type": "string",
                    "enum": ["inbound", "outbound"],
                },
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["lead_id", "activity_type"],
        },
    },
]


def _auth() -> tuple[str, str]:
    return (os.getenv("CLOSE_API_KEY", ""), "")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("CLOSE_API_KEY", "")
    if not api_key:
        return {"error": "CLOSE_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            auth=_auth(),
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        ) as c:
            if tool_name == "close_list_leads":
                params: dict[str, Any] = {
                    "_limit": arguments.get("_limit", 25),
                    "_skip": arguments.get("_skip", 0),
                }
                if q := arguments.get("query"):
                    params["query"] = q
                r = await c.get(f"{CLOSE_BASE}/lead/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "close_create_lead":
                body: dict[str, Any] = {"name": arguments["name"]}
                for k in ("url", "description", "contacts", "custom", "status_id"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post(f"{CLOSE_BASE}/lead/", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "close_update_lead":
                lid = arguments["lead_id"]
                body = {
                    k: arguments[k]
                    for k in ("name", "description", "status_id", "custom")
                    if k in arguments
                }
                r = await c.put(f"{CLOSE_BASE}/lead/{lid}/", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "close_list_contacts":
                params = {
                    "_limit": arguments.get("_limit", 25),
                    "_skip": arguments.get("_skip", 0),
                }
                if lid := arguments.get("lead_id"):
                    params["lead_id"] = lid
                r = await c.get(f"{CLOSE_BASE}/contact/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "close_create_contact":
                body = {
                    "lead_id": arguments["lead_id"],
                    "name": arguments["name"],
                }
                for k in ("title", "emails", "phones"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post(f"{CLOSE_BASE}/contact/", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "close_create_activity":
                atype = arguments.get("activity_type", "note")
                body = {"lead_id": arguments["lead_id"]}
                for k in ("note", "status", "direction", "subject", "body"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post(f"{CLOSE_BASE}/activity/{atype}/", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("close_crm_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
