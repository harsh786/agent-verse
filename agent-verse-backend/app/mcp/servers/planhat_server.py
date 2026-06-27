"""Planhat MCP server — companies, end-users, and activity logging.

Environment variables:
  PLANHAT_API_KEY: Planhat API key (Bearer token)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

PLANHAT_BASE = "https://api.planhat.com"

TOOL_DEFINITIONS = [
    {
        "name": "planhat_list_companies",
        "description": "List companies (customers) in Planhat",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
                "sort": {"type": "string", "description": "Sort field, e.g. -mrr"},
            },
        },
    },
    {
        "name": "planhat_create_company",
        "description": "Create a new company in Planhat",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "externalId": {"type": "string", "description": "Your internal company ID"},
                "mrr": {"type": "number", "description": "Monthly recurring revenue"},
                "phase": {"type": "string", "description": "Customer lifecycle phase"},
                "owner": {"type": "string", "description": "Owner user ID or email"},
                "custom": {"type": "object", "description": "Custom field values"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "planhat_list_endusers",
        "description": "List end-users (contacts) in Planhat",
        "parameters": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string", "description": "Filter by Planhat company ID"},
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "planhat_create_enduser",
        "description": "Create a new end-user linked to a company in Planhat",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "company_id": {"type": "string", "description": "Planhat company ID"},
                "externalId": {"type": "string"},
                "role": {"type": "string"},
            },
            "required": ["name", "company_id"],
        },
    },
    {
        "name": "planhat_log_activity",
        "description": "Log a customer activity or touchpoint in Planhat",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Activity type or action name"},
                "company_id": {"type": "string", "description": "Planhat company ID"},
                "enduser_id": {"type": "string"},
                "description": {"type": "string"},
                "weight": {"type": "number", "description": "Activity weight/score"},
                "timestamp": {
                    "type": "string",
                    "description": "ISO 8601 datetime; defaults to now",
                },
            },
            "required": ["action", "company_id"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("PLANHAT_API_KEY", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("PLANHAT_API_KEY", "")
    if not token:
        return {"error": "PLANHAT_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=PLANHAT_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "planhat_list_companies":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if sort := arguments.get("sort"):
                    params["sort"] = sort
                r = await c.get("/companies", params=params)
                r.raise_for_status()
                return {"companies": r.json()}

            elif tool_name == "planhat_create_company":
                body: dict[str, Any] = {"name": arguments["name"]}
                for k in ("externalId", "mrr", "phase", "owner", "custom"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/companies", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "planhat_list_endusers":
                params = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if cid := arguments.get("company_id"):
                    params["companyId"] = cid
                r = await c.get("/endusers", params=params)
                r.raise_for_status()
                return {"endusers": r.json()}

            elif tool_name == "planhat_create_enduser":
                body = {
                    "name": arguments["name"],
                    "companyId": arguments["company_id"],
                }
                for k in ("email", "externalId", "role"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/endusers", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "planhat_log_activity":
                body = {
                    "action": arguments["action"],
                    "companyId": arguments["company_id"],
                }
                for k in ("description", "weight", "timestamp"):
                    if k in arguments:
                        body[k] = arguments[k]
                if eu := arguments.get("enduser_id"):
                    body["enduserId"] = eu
                r = await c.post("/activities", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("planhat_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
