"""Copper CRM MCP server — people, companies, and opportunities.

Environment variables:
  COPPER_API_KEY: Copper API key
  COPPER_USER_EMAIL: Email address of the Copper user making API calls
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

COPPER_BASE = "https://api.copper.com/developer_api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "copper_list_people",
        "description": "List people (contacts) in Copper CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "default": 20},
                "page_number": {"type": "integer", "default": 1},
                "sort_by": {"type": "string"},
                "sort_direction": {"type": "string", "enum": ["asc", "desc"]},
            },
        },
    },
    {
        "name": "copper_create_person",
        "description": "Create a new person (contact) in Copper CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "emails": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "[{email: '...', category: 'work'}]",
                },
                "phone_numbers": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "title": {"type": "string"},
                "company_id": {"type": "integer"},
                "company_name": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "copper_list_companies",
        "description": "List companies in Copper CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "default": 20},
                "page_number": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "copper_list_opportunities",
        "description": "List opportunities (deals) in Copper CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "default": 20},
                "page_number": {"type": "integer", "default": 1},
                "sort_by": {"type": "string"},
                "sort_direction": {"type": "string", "enum": ["asc", "desc"]},
            },
        },
    },
    {
        "name": "copper_create_opportunity",
        "description": "Create a new opportunity in Copper CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "primary_contact_id": {"type": "integer"},
                "company_id": {"type": "integer"},
                "monetary_value": {"type": "number"},
                "pipeline_id": {"type": "integer"},
                "pipeline_stage_id": {"type": "integer"},
                "status": {
                    "type": "string",
                    "enum": ["Open", "Won", "Lost", "Abandoned"],
                    "default": "Open",
                },
            },
            "required": ["name"],
        },
    },
]


def _headers() -> dict[str, str]:
    return {
        "X-PW-AccessToken": os.getenv("COPPER_API_KEY", ""),
        "X-PW-Application": "developer_api",
        "X-PW-UserEmail": os.getenv("COPPER_USER_EMAIL", ""),
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("COPPER_API_KEY") or not os.getenv("COPPER_USER_EMAIL"):
        return {"error": "COPPER_API_KEY and COPPER_USER_EMAIL required"}

    try:
        async with httpx.AsyncClient(
            base_url=COPPER_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "copper_list_people":
                body: dict[str, Any] = {
                    "page_size": arguments.get("page_size", 20),
                    "page_number": arguments.get("page_number", 1),
                }
                for k in ("sort_by", "sort_direction"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/people/search", json=body)
                r.raise_for_status()
                return {"people": r.json()}

            elif tool_name == "copper_create_person":
                body = {"name": arguments["name"]}
                for k in ("emails", "phone_numbers", "title", "company_id", "company_name"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/people", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "copper_list_companies":
                body = {
                    "page_size": arguments.get("page_size", 20),
                    "page_number": arguments.get("page_number", 1),
                }
                r = await c.post("/companies/search", json=body)
                r.raise_for_status()
                return {"companies": r.json()}

            elif tool_name == "copper_list_opportunities":
                body = {
                    "page_size": arguments.get("page_size", 20),
                    "page_number": arguments.get("page_number", 1),
                }
                for k in ("sort_by", "sort_direction"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/opportunities/search", json=body)
                r.raise_for_status()
                return {"opportunities": r.json()}

            elif tool_name == "copper_create_opportunity":
                body = {"name": arguments["name"]}
                for k in (
                    "primary_contact_id",
                    "company_id",
                    "monetary_value",
                    "pipeline_id",
                    "pipeline_stage_id",
                    "status",
                ):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/opportunities", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("copper_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
