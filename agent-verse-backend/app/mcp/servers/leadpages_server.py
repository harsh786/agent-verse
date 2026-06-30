"""Leadpages MCP server — landing pages, leads, lead boxes, and conversion analytics.

Environment variables:
  LEADPAGES_API_KEY: Leadpages API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

LEADPAGES_BASE = "https://api.leadpages.io/v1"

TOOL_DEFINITIONS = [
    {
        "name": "leadpages_list_pages",
        "description": "List all Leadpages landing pages in your account",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 25},
                "offset": {"type": "integer", "default": 0},
                "status": {
                    "type": "string",
                    "enum": ["published", "draft", "all"],
                    "default": "all",
                },
                "sort_by": {"type": "string", "description": "Sort field, e.g. 'created_at'"},
            },
        },
    },
    {
        "name": "leadpages_create_page",
        "description": "Create a new landing page in Leadpages from a template",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Page name"},
                "template_id": {"type": "string", "description": "Template ID to base the page on"},
                "subdomain": {"type": "string", "description": "Leadpages subdomain slug"},
                "title": {"type": "string", "description": "SEO page title"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "leadpages_get_page_stats",
        "description": "Get performance statistics (views, conversions, conversion rate) for a landing page",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Leadpages page ID"},
                "start_date": {"type": "string", "description": "Stats start date (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "Stats end date (YYYY-MM-DD)"},
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "leadpages_list_leads",
        "description": "List leads (form submissions) captured by Leadpages",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Filter leads by page ID"},
                "limit": {"type": "integer", "default": 25},
                "offset": {"type": "integer", "default": 0},
                "start_date": {"type": "string", "description": "Filter by submission date (YYYY-MM-DD)"},
                "end_date": {"type": "string"},
            },
        },
    },
    {
        "name": "leadpages_create_lead_box",
        "description": "Create a Leadbox (pop-up opt-in form) in Leadpages",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Leadbox name"},
                "template_id": {"type": "string", "description": "Template ID"},
                "trigger_type": {
                    "type": "string",
                    "enum": ["click", "timed", "exit", "scroll"],
                    "default": "click",
                },
                "trigger_delay": {"type": "integer", "description": "Delay in seconds (for timed trigger)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "leadpages_get_conversions",
        "description": "Get conversion analytics for all pages or a specific page in Leadpages",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Optional page ID filter"},
                "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "group_by": {
                    "type": "string",
                    "enum": ["day", "week", "month"],
                    "default": "day",
                },
            },
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("LEADPAGES_API_KEY", "")
    if not api_key:
        return {"error": "LEADPAGES_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=LEADPAGES_BASE, headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "leadpages_list_pages":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                if status := arguments.get("status", "all"):
                    if status != "all":
                        params["status"] = status
                if "sort_by" in arguments:
                    params["sort_by"] = arguments["sort_by"]
                r = await c.get("/pages", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "leadpages_create_page":
                body: dict[str, Any] = {"name": arguments["name"]}
                for k in ("template_id", "subdomain", "title"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/pages", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "leadpages_get_page_stats":
                pid = arguments["page_id"]
                params = {}
                for k in ("start_date", "end_date"):
                    if k in arguments:
                        params[k] = arguments[k]
                r = await c.get(f"/pages/{pid}/stats", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "leadpages_list_leads":
                params = {
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                for k in ("page_id", "start_date", "end_date"):
                    if k in arguments:
                        params[k] = arguments[k]
                r = await c.get("/leads", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "leadpages_create_lead_box":
                body = {
                    "name": arguments["name"],
                    "trigger_type": arguments.get("trigger_type", "click"),
                }
                for k in ("template_id", "trigger_delay"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/leadboxes", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "leadpages_get_conversions":
                params = {"group_by": arguments.get("group_by", "day")}
                for k in ("page_id", "start_date", "end_date"):
                    if k in arguments:
                        params[k] = arguments[k]
                r = await c.get("/analytics/conversions", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("leadpages_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
