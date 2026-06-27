"""Freshservice MCP server — IT service management (ITSM) integration.

Environment:
  FRESHSERVICE_DOMAIN:  Freshservice subdomain (e.g. 'mycompany')
  FRESHSERVICE_API_KEY: API key
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base() -> str:
    domain = os.getenv("FRESHSERVICE_DOMAIN", "")
    return f"https://{domain}.freshservice.com/api/v2"


def _headers() -> dict[str, str]:
    api_key = os.getenv("FRESHSERVICE_API_KEY", "")
    creds = base64.b64encode(f"{api_key}:X".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "freshservice_list_tickets",
        "description": "List Freshservice IT tickets",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 30},
                "order_by": {"type": "string", "default": "created_at"},
                "order_type": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
            },
        },
    },
    {
        "name": "freshservice_get_ticket",
        "description": "Get a Freshservice ticket by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "freshservice_create_ticket",
        "description": "Create a new Freshservice IT support ticket",
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
                "category": {"type": "string"},
                "sub_category": {"type": "string"},
            },
            "required": ["subject", "email"],
        },
    },
    {
        "name": "freshservice_update_ticket",
        "description": "Update a Freshservice ticket",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "status": {"type": "integer"},
                "priority": {"type": "integer"},
                "agent_id": {"type": "integer"},
                "group_id": {"type": "integer"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "freshservice_list_assets",
        "description": "List IT assets tracked in Freshservice",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 30},
                "asset_type_id": {"type": "integer"},
            },
        },
    },
    {
        "name": "freshservice_list_changes",
        "description": "List change requests in Freshservice",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 30},
                "status": {"type": "integer"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    domain = os.getenv("FRESHSERVICE_DOMAIN", "")
    api_key = os.getenv("FRESHSERVICE_API_KEY", "")
    if not domain or not api_key:
        return {"error": "FRESHSERVICE_DOMAIN and FRESHSERVICE_API_KEY must be configured"}

    base = _base()

    try:
        async with httpx.AsyncClient(headers=_headers(), timeout=30.0) as c:
            if tool_name == "freshservice_list_tickets":
                r = await c.get(
                    f"{base}/tickets",
                    params={
                        "page": arguments.get("page", 1),
                        "per_page": arguments.get("per_page", 30),
                        "order_by": arguments.get("order_by", "created_at"),
                        "order_type": arguments.get("order_type", "desc"),
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshservice_get_ticket":
                r = await c.get(f"{base}/tickets/{arguments['ticket_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshservice_create_ticket":
                payload: dict[str, Any] = {
                    "subject": arguments["subject"],
                    "email": arguments["email"],
                    "priority": arguments.get("priority", 2),
                    "status": arguments.get("status", 2),
                }
                if desc := arguments.get("description"):
                    payload["description"] = desc
                for field in ["category", "sub_category"]:
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.post(f"{base}/tickets", json={"ticket": payload})
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshservice_update_ticket":
                tid = arguments["ticket_id"]
                payload = {}
                for field in ["status", "priority", "agent_id", "group_id"]:
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await c.put(f"{base}/tickets/{tid}", json={"ticket": payload})
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshservice_list_assets":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 30),
                }
                if at := arguments.get("asset_type_id"):
                    params["asset_type_id"] = at
                r = await c.get(f"{base}/assets", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshservice_list_changes":
                params = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 30),
                }
                if status := arguments.get("status"):
                    params["status"] = status
                r = await c.get(f"{base}/changes", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("freshservice_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
