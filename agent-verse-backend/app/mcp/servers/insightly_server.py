"""Insightly CRM MCP server — contacts, opportunities, projects, and tasks.

Environment variables:
  INSIGHTLY_API_KEY: Insightly API key (base64-encoded as Basic auth password)
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

INSIGHTLY_BASE = "https://api.insight.ly/v3.1"

TOOL_DEFINITIONS = [
    {
        "name": "insightly_list_contacts",
        "description": "List contacts in Insightly CRM with optional search and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "top": {"type": "integer", "description": "Max contacts to return", "default": 25},
                "skip": {"type": "integer", "description": "Records to skip for pagination", "default": 0},
                "search": {"type": "string", "description": "Search term for contact name or email"},
                "field_name": {"type": "string", "description": "Field name to filter by"},
                "field_value": {"type": "string", "description": "Field value to filter by"},
            },
        },
    },
    {
        "name": "insightly_create_contact",
        "description": "Create a new contact in Insightly CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "FIRST_NAME": {"type": "string"},
                "LAST_NAME": {"type": "string"},
                "EMAIL_ADDRESS": {"type": "string"},
                "PHONE": {"type": "string"},
                "TITLE": {"type": "string"},
                "ORGANISATION_ID": {"type": "integer", "description": "Link to an organisation"},
            },
        },
    },
    {
        "name": "insightly_list_opportunities",
        "description": "List sales opportunities in Insightly CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "top": {"type": "integer", "default": 25},
                "skip": {"type": "integer", "default": 0},
                "search": {"type": "string"},
                "OPPORTUNITY_STATE": {
                    "type": "string",
                    "enum": ["OPEN", "WON", "ABANDONED", "SUSPENDED", "LOST"],
                    "description": "Filter by opportunity state",
                },
            },
        },
    },
    {
        "name": "insightly_create_opportunity",
        "description": "Create a new sales opportunity in Insightly CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "OPPORTUNITY_NAME": {"type": "string", "description": "Opportunity name"},
                "BID_AMOUNT": {"type": "number", "description": "Deal value"},
                "BID_CURRENCY": {"type": "string", "default": "USD"},
                "FORECAST_CLOSE_DATE": {"type": "string", "description": "Forecast close date (YYYY-MM-DD)"},
                "PROBABILITY": {"type": "integer", "description": "Win probability 0-100"},
                "PIPELINE_ID": {"type": "integer"},
                "STAGE_ID": {"type": "integer"},
            },
            "required": ["OPPORTUNITY_NAME"],
        },
    },
    {
        "name": "insightly_list_projects",
        "description": "List projects in Insightly CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "top": {"type": "integer", "default": 25},
                "skip": {"type": "integer", "default": 0},
                "search": {"type": "string"},
                "STATUS": {"type": "string", "description": "Filter by status, e.g. 'In Progress'"},
            },
        },
    },
    {
        "name": "insightly_create_task",
        "description": "Create a task in Insightly CRM, optionally linked to a contact or opportunity",
        "parameters": {
            "type": "object",
            "properties": {
                "TITLE": {"type": "string", "description": "Task title"},
                "DUE_DATE": {"type": "string", "description": "Due date/time (ISO 8601)"},
                "STATUS": {"type": "string", "default": "NOT STARTED"},
                "PRIORITY": {"type": "integer", "description": "Priority 1 (low) – 3 (high)", "default": 2},
                "RESPONSIBLE_USER_ID": {"type": "integer"},
                "LINKED_CONTACT_ID": {"type": "integer"},
                "LINKED_OPPORTUNITY_ID": {"type": "integer"},
                "DETAILS": {"type": "string", "description": "Task notes/description"},
            },
            "required": ["TITLE"],
        },
    },
]


def _auth(api_key: str) -> tuple[str, str]:
    # Insightly uses the API key as the password in HTTP Basic auth with empty username
    return (api_key, "")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("INSIGHTLY_API_KEY", "")
    if not api_key:
        return {"error": "INSIGHTLY_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=INSIGHTLY_BASE,
            auth=_auth(api_key),
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        ) as c:
            if tool_name == "insightly_list_contacts":
                params: dict[str, Any] = {
                    "$top": arguments.get("top", 25),
                    "$skip": arguments.get("skip", 0),
                }
                if search := arguments.get("search"):
                    params["$search"] = search
                if "field_name" in arguments and "field_value" in arguments:
                    params["field_name"] = arguments["field_name"]
                    params["field_value"] = arguments["field_value"]
                r = await c.get("/Contacts", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "insightly_create_contact":
                body: dict[str, Any] = {}
                for k in ("FIRST_NAME", "LAST_NAME", "EMAIL_ADDRESS", "PHONE", "TITLE", "ORGANISATION_ID"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/Contacts", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "insightly_list_opportunities":
                params = {
                    "$top": arguments.get("top", 25),
                    "$skip": arguments.get("skip", 0),
                }
                if search := arguments.get("search"):
                    params["$search"] = search
                if state := arguments.get("OPPORTUNITY_STATE"):
                    params["OPPORTUNITY_STATE"] = state
                r = await c.get("/Opportunities", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "insightly_create_opportunity":
                body = {"OPPORTUNITY_NAME": arguments["OPPORTUNITY_NAME"]}
                for k in ("BID_AMOUNT", "BID_CURRENCY", "FORECAST_CLOSE_DATE",
                          "PROBABILITY", "PIPELINE_ID", "STAGE_ID"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/Opportunities", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "insightly_list_projects":
                params = {
                    "$top": arguments.get("top", 25),
                    "$skip": arguments.get("skip", 0),
                }
                if search := arguments.get("search"):
                    params["$search"] = search
                if status := arguments.get("STATUS"):
                    params["STATUS"] = status
                r = await c.get("/Projects", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "insightly_create_task":
                body = {"TITLE": arguments["TITLE"]}
                for k in ("DUE_DATE", "STATUS", "PRIORITY", "RESPONSIBLE_USER_ID",
                          "LINKED_CONTACT_ID", "LINKED_OPPORTUNITY_ID", "DETAILS"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/Tasks", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("insightly_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
