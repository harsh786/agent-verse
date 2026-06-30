"""Autopilot (Ortto) MCP server — marketing automation, journeys, and contact management.

Environment:
  AUTOPILOT_API_KEY: Autopilot API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api2.autopilothq.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "autopilot_add_contact",
        "description": "Add a new contact to Autopilot or update if they already exist",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address"},
                "first_name": {"type": "string", "description": "First name"},
                "last_name": {"type": "string", "description": "Last name"},
                "phone": {"type": "string", "description": "Phone number"},
                "company": {"type": "string", "description": "Company name"},
                "custom_fields": {"type": "object", "description": "Custom field key-value pairs"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "autopilot_update_contact",
        "description": "Update fields on an existing Autopilot contact",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "Autopilot contact ID"},
                "email": {"type": "string", "description": "Email to identify contact"},
                "fields": {"type": "object", "description": "Fields to update as key-value pairs"},
            },
        },
    },
    {
        "name": "autopilot_add_to_list",
        "description": "Add a contact to a specific Autopilot list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "Autopilot list ID"},
                "contact_id": {"type": "string", "description": "Contact ID to add to the list"},
            },
            "required": ["list_id", "contact_id"],
        },
    },
    {
        "name": "autopilot_remove_from_list",
        "description": "Remove a contact from an Autopilot list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "Autopilot list ID"},
                "contact_id": {"type": "string", "description": "Contact ID to remove"},
            },
            "required": ["list_id", "contact_id"],
        },
    },
    {
        "name": "autopilot_trigger_journey",
        "description": "Enroll a contact in an Autopilot journey (automation sequence)",
        "parameters": {
            "type": "object",
            "properties": {
                "journey_id": {"type": "string", "description": "Autopilot journey ID"},
                "contact_id": {"type": "string", "description": "Contact ID to enroll"},
            },
            "required": ["journey_id", "contact_id"],
        },
    },
    {
        "name": "autopilot_get_contact_stats",
        "description": "Get statistics and engagement data for a contact",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "Contact ID"},
            },
            "required": ["contact_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("AUTOPILOT_API_KEY", "")
    if not api_key:
        return {"error": "AUTOPILOT_API_KEY not configured"}

    headers = {"autopilotapikey": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "autopilot_add_contact":
                contact: dict[str, Any] = {"Email": arguments["email"]}
                if "first_name" in arguments:
                    contact["FirstName"] = arguments["first_name"]
                if "last_name" in arguments:
                    contact["LastName"] = arguments["last_name"]
                if "phone" in arguments:
                    contact["Phone"] = arguments["phone"]
                if "company" in arguments:
                    contact["Company"] = arguments["company"]
                if "custom_fields" in arguments:
                    contact.update(arguments["custom_fields"])
                r = await client.post(
                    f"{BASE_URL}/contact",
                    headers=headers,
                    json={"contact": contact},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "autopilot_update_contact":
                contact = {}
                if "email" in arguments:
                    contact["Email"] = arguments["email"]
                if "fields" in arguments:
                    contact.update(arguments["fields"])
                contact_id = arguments.get("contact_id", arguments.get("email", ""))
                r = await client.post(
                    f"{BASE_URL}/contact",
                    headers=headers,
                    json={"contact": contact},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "autopilot_add_to_list":
                r = await client.post(
                    f"{BASE_URL}/list/{arguments['list_id']}/contact/{arguments['contact_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return {"added": True}

            if tool_name == "autopilot_remove_from_list":
                r = await client.delete(
                    f"{BASE_URL}/list/{arguments['list_id']}/contact/{arguments['contact_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return {"removed": True}

            if tool_name == "autopilot_trigger_journey":
                r = await client.post(
                    f"{BASE_URL}/trigger/{arguments['journey_id']}/contact/{arguments['contact_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "autopilot_get_contact_stats":
                r = await client.get(
                    f"{BASE_URL}/contact/{arguments['contact_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
