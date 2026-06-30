"""Lemlist MCP server — sales outreach: campaigns, leads, and engagement analytics.

Environment variables:
  LEMLIST_API_KEY: Lemlist API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

LEMLIST_BASE = "https://api.lemlist.com/api"

TOOL_DEFINITIONS = [
    {
        "name": "lemlist_list_campaigns",
        "description": "List all email outreach campaigns in Lemlist",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 25},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "lemlist_create_campaign",
        "description": "Create a new outreach campaign in Lemlist",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Campaign name"},
                "sendingSchedule": {
                    "type": "object",
                    "description": "Sending schedule configuration (days and time windows)",
                },
                "maxNewLeadsPerDay": {"type": "integer", "description": "Max new leads to contact per day", "default": 50},
            },
            "required": ["name"],
        },
    },
    {
        "name": "lemlist_add_lead",
        "description": "Add a lead to a Lemlist campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Lemlist campaign ID (camp_...)"},
                "email": {"type": "string", "description": "Lead's email address"},
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
                "companyName": {"type": "string"},
                "icebreaker": {"type": "string", "description": "Personalised icebreaker line"},
                "custom_fields": {"type": "object", "description": "Additional custom variables"},
            },
            "required": ["campaign_id", "email"],
        },
    },
    {
        "name": "lemlist_list_leads",
        "description": "List leads in a Lemlist campaign with their current status",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Campaign ID to list leads from"},
                "limit": {"type": "integer", "default": 25},
                "offset": {"type": "integer", "default": 0},
                "status": {
                    "type": "string",
                    "enum": ["all", "contacted", "interested", "notInterested", "completed"],
                    "default": "all",
                },
            },
            "required": ["campaign_id"],
        },
    },
    {
        "name": "lemlist_get_campaign_stats",
        "description": "Get performance statistics for a Lemlist campaign (sent, opened, clicked, replied)",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Campaign ID"},
            },
            "required": ["campaign_id"],
        },
    },
    {
        "name": "lemlist_pause_campaign",
        "description": "Pause an active Lemlist campaign to stop sending emails",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Campaign ID to pause"},
            },
            "required": ["campaign_id"],
        },
    },
]


def _auth(api_key: str) -> tuple[str, str]:
    return ("", api_key)


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("LEMLIST_API_KEY", "")
    if not api_key:
        return {"error": "LEMLIST_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=LEMLIST_BASE,
            auth=_auth(api_key),
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        ) as c:
            if tool_name == "lemlist_list_campaigns":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                r = await c.get("/campaigns", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "lemlist_create_campaign":
                body: dict[str, Any] = {
                    "name": arguments["name"],
                    "maxNewLeadsPerDay": arguments.get("maxNewLeadsPerDay", 50),
                }
                if "sendingSchedule" in arguments:
                    body["sendingSchedule"] = arguments["sendingSchedule"]
                r = await c.post("/campaigns", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "lemlist_add_lead":
                camp_id = arguments["campaign_id"]
                body = {"email": arguments["email"]}
                for k in ("firstName", "lastName", "companyName", "icebreaker"):
                    if k in arguments:
                        body[k] = arguments[k]
                if "custom_fields" in arguments:
                    body.update(arguments["custom_fields"])
                r = await c.post(f"/campaigns/{camp_id}/leads/{arguments['email']}", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "lemlist_list_leads":
                camp_id = arguments["campaign_id"]
                params = {
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                if status := arguments.get("status", "all"):
                    if status != "all":
                        params["status"] = status
                r = await c.get(f"/campaigns/{camp_id}/leads", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "lemlist_get_campaign_stats":
                camp_id = arguments["campaign_id"]
                r = await c.get(f"/campaigns/{camp_id}/stats")
                r.raise_for_status()
                return r.json()

            elif tool_name == "lemlist_pause_campaign":
                camp_id = arguments["campaign_id"]
                r = await c.post(f"/campaigns/{camp_id}/pause")
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("lemlist_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
