"""GoHighLevel CRM MCP server — contacts, campaigns, pipelines, opportunities, and SMS.

Environment variables:
  HIGHLEVEL_API_KEY: GoHighLevel API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

HIGHLEVEL_BASE = "https://rest.gohighlevel.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "highlevel_list_contacts",
        "description": "List contacts in GoHighLevel CRM with optional search and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for name, email, or phone"},
                "limit": {"type": "integer", "description": "Max contacts to return", "default": 20},
                "skip": {"type": "integer", "description": "Number of records to skip", "default": 0},
                "locationId": {"type": "string", "description": "Sub-account/location ID"},
            },
        },
    },
    {
        "name": "highlevel_create_contact",
        "description": "Create a new contact in GoHighLevel CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string", "description": "Phone number in E.164 format"},
                "companyName": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source": {"type": "string", "description": "Lead source"},
                "locationId": {"type": "string"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "highlevel_add_contact_to_campaign",
        "description": "Add a contact to a marketing campaign in GoHighLevel",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "GoHighLevel contact ID"},
                "campaign_id": {"type": "string", "description": "Campaign ID to enroll the contact in"},
            },
            "required": ["contact_id", "campaign_id"],
        },
    },
    {
        "name": "highlevel_list_pipelines",
        "description": "List all sales pipelines in GoHighLevel",
        "parameters": {
            "type": "object",
            "properties": {
                "locationId": {"type": "string", "description": "Sub-account location ID"},
            },
        },
    },
    {
        "name": "highlevel_create_opportunity",
        "description": "Create a new opportunity in a GoHighLevel pipeline",
        "parameters": {
            "type": "object",
            "properties": {
                "pipelineId": {"type": "string", "description": "Pipeline ID"},
                "locationId": {"type": "string"},
                "name": {"type": "string", "description": "Opportunity name"},
                "pipelineStageId": {"type": "string", "description": "Pipeline stage ID"},
                "status": {
                    "type": "string",
                    "enum": ["open", "won", "lost", "abandoned"],
                    "default": "open",
                },
                "monetaryValue": {"type": "number"},
                "assignedTo": {"type": "string", "description": "Assigned user ID"},
                "contactId": {"type": "string", "description": "Associated contact ID"},
            },
            "required": ["pipelineId", "name"],
        },
    },
    {
        "name": "highlevel_send_sms",
        "description": "Send an SMS message to a contact via GoHighLevel",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["SMS"], "default": "SMS"},
                "contactId": {"type": "string", "description": "Contact ID to send SMS to"},
                "message": {"type": "string", "description": "SMS message text"},
                "fromNumber": {"type": "string", "description": "Sender phone number"},
            },
            "required": ["contactId", "message"],
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("HIGHLEVEL_API_KEY", "")
    if not api_key:
        return {"error": "HIGHLEVEL_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=HIGHLEVEL_BASE, headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "highlevel_list_contacts":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 20),
                    "skip": arguments.get("skip", 0),
                }
                if q := arguments.get("query"):
                    params["query"] = q
                if lid := arguments.get("locationId"):
                    params["locationId"] = lid
                r = await c.get("/contacts/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "highlevel_create_contact":
                body: dict[str, Any] = {"email": arguments["email"]}
                for k in ("firstName", "lastName", "phone", "companyName", "tags", "source", "locationId"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/contacts/", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "highlevel_add_contact_to_campaign":
                cid = arguments["contact_id"]
                camp_id = arguments["campaign_id"]
                r = await c.post(f"/contacts/{cid}/campaigns/{camp_id}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "highlevel_list_pipelines":
                params = {}
                if lid := arguments.get("locationId"):
                    params["locationId"] = lid
                r = await c.get("/pipelines/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "highlevel_create_opportunity":
                body = {
                    "pipelineId": arguments["pipelineId"],
                    "name": arguments["name"],
                    "status": arguments.get("status", "open"),
                }
                for k in ("locationId", "pipelineStageId", "monetaryValue", "assignedTo", "contactId"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/opportunities/", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "highlevel_send_sms":
                body = {
                    "type": "SMS",
                    "contactId": arguments["contactId"],
                    "message": arguments["message"],
                }
                if "fromNumber" in arguments:
                    body["fromNumber"] = arguments["fromNumber"]
                r = await c.post("/conversations/messages", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("highlevel_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
