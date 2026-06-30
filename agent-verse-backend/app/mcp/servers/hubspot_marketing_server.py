"""HubSpot Marketing Hub MCP server — forms, email campaigns, lists, and analytics.

Environment variables:
  HUBSPOT_API_KEY: HubSpot private app access token (or legacy API key)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

HUBSPOT_BASE = "https://api.hubapi.com"

TOOL_DEFINITIONS = [
    {
        "name": "hubspot_marketing_list_forms",
        "description": "List all marketing forms in HubSpot Marketing Hub",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max forms to return", "default": 25},
                "offset": {"type": "integer", "default": 0},
                "formTypes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by form type: ['hubspot', 'captured', 'flow', 'blog_comment', 'all']",
                },
            },
        },
    },
    {
        "name": "hubspot_marketing_get_form_submissions",
        "description": "Get submissions for a specific HubSpot form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string", "description": "HubSpot form GUID"},
                "limit": {"type": "integer", "default": 20},
                "after": {"type": "string", "description": "Pagination cursor"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "hubspot_marketing_list_email_campaigns",
        "description": "List email marketing campaigns in HubSpot",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 25},
                "offset": {"type": "integer", "default": 0},
                "orderBy": {"type": "string", "description": "Sort order field"},
            },
        },
    },
    {
        "name": "hubspot_marketing_get_campaign_stats",
        "description": "Get performance statistics for a HubSpot email campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID"},
                "appId": {"type": "integer", "description": "App ID (optional)"},
            },
            "required": ["campaign_id"],
        },
    },
    {
        "name": "hubspot_marketing_create_list",
        "description": "Create a static or dynamic contact list in HubSpot",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "List name"},
                "dynamic": {"type": "boolean", "description": "True for active/dynamic list, False for static", "default": False},
                "filters": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Filter rules for dynamic lists",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "hubspot_marketing_add_to_list",
        "description": "Add contacts to a static HubSpot contact list by email addresses",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "integer", "description": "Static list ID"},
                "emails": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Email addresses to add to the list",
                },
                "vids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "HubSpot contact VIDs to add",
                },
            },
            "required": ["list_id"],
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("HUBSPOT_API_KEY", "")
    if not api_key:
        return {"error": "HUBSPOT_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=HUBSPOT_BASE, headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "hubspot_marketing_list_forms":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                if "formTypes" in arguments:
                    params["formTypes"] = ",".join(arguments["formTypes"])
                r = await c.get("/forms/v2/forms", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_marketing_get_form_submissions":
                form_id = arguments["form_id"]
                params = {"limit": arguments.get("limit", 20)}
                if "after" in arguments:
                    params["after"] = arguments["after"]
                r = await c.get(f"/form-integrations/v1/submissions/forms/{form_id}", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_marketing_list_email_campaigns":
                params = {
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                if "orderBy" in arguments:
                    params["orderBy"] = arguments["orderBy"]
                r = await c.get("/email/public/v1/campaigns", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_marketing_get_campaign_stats":
                cid = arguments["campaign_id"]
                params = {}
                if "appId" in arguments:
                    params["appId"] = arguments["appId"]
                r = await c.get(f"/email/public/v1/campaigns/{cid}", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_marketing_create_list":
                body: dict[str, Any] = {
                    "name": arguments["name"],
                    "dynamic": arguments.get("dynamic", False),
                }
                if "filters" in arguments:
                    body["filters"] = arguments["filters"]
                r = await c.post("/contacts/v1/lists", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_marketing_add_to_list":
                lid = arguments["list_id"]
                body = {}
                if "emails" in arguments:
                    body["emails"] = arguments["emails"]
                if "vids" in arguments:
                    body["vids"] = arguments["vids"]
                r = await c.post(f"/contacts/v1/lists/{lid}/add", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("hubspot_marketing_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
