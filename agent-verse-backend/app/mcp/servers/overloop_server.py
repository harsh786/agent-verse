"""Overloop (ex-Prospect.io) MCP server — outreach prospecting and sequence management.

Environment variables:
  OVERLOOP_API_KEY: Overloop API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

OVERLOOP_BASE = "https://app.overloop.com/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "overloop_list_prospects",
        "description": "List prospects in Overloop with optional search and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
                "email": {"type": "string", "description": "Filter by email address"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
            },
        },
    },
    {
        "name": "overloop_create_prospect",
        "description": "Create a new prospect in Overloop for outreach",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Prospect's email address"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "company": {"type": "string"},
                "title": {"type": "string"},
                "phone": {"type": "string"},
                "linkedin_url": {"type": "string"},
                "custom_attributes": {"type": "object", "description": "Custom attribute key-value pairs"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "overloop_list_campaigns",
        "description": "List email outreach campaigns in Overloop",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "draft", "all"],
                    "default": "all",
                },
            },
        },
    },
    {
        "name": "overloop_enroll_prospect",
        "description": "Enrol a prospect into an Overloop campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "prospect_id": {"type": "integer", "description": "Prospect ID"},
                "campaign_id": {"type": "integer", "description": "Campaign ID"},
            },
            "required": ["prospect_id", "campaign_id"],
        },
    },
    {
        "name": "overloop_get_campaign_stats",
        "description": "Get performance statistics for an Overloop campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID"},
            },
            "required": ["campaign_id"],
        },
    },
    {
        "name": "overloop_list_sequences",
        "description": "List email sequences in Overloop",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
            },
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("OVERLOOP_API_KEY", "")
    if not api_key:
        return {"error": "OVERLOOP_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=OVERLOOP_BASE, headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "overloop_list_prospects":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                }
                for k in ("email", "first_name", "last_name"):
                    if k in arguments:
                        params[k] = arguments[k]
                r = await c.get("/prospects", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "overloop_create_prospect":
                body: dict[str, Any] = {"email": arguments["email"]}
                for k in ("first_name", "last_name", "company", "title", "phone", "linkedin_url"):
                    if k in arguments:
                        body[k] = arguments[k]
                if "custom_attributes" in arguments:
                    body["custom_attributes"] = arguments["custom_attributes"]
                r = await c.post("/prospects", json={"prospect": body})
                r.raise_for_status()
                return r.json()

            elif tool_name == "overloop_list_campaigns":
                params = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                }
                if status := arguments.get("status", "all"):
                    if status != "all":
                        params["status"] = status
                r = await c.get("/campaigns", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "overloop_enroll_prospect":
                body = {
                    "prospect_id": arguments["prospect_id"],
                    "campaign_id": arguments["campaign_id"],
                }
                r = await c.post("/campaign_subscriptions", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "overloop_get_campaign_stats":
                cid = arguments["campaign_id"]
                r = await c.get(f"/campaigns/{cid}/stats")
                r.raise_for_status()
                return r.json()

            elif tool_name == "overloop_list_sequences":
                params = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                }
                r = await c.get("/sequences", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("overloop_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
