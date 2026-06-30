"""Loops MCP server — transactional and marketing email for SaaS products.

Environment:
  LOOPS_API_KEY: Loops API key from Settings > API
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://app.loops.so/api/v1"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('LOOPS_API_KEY', '')}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "loops_create_contact",
        "description": "Create a new contact in Loops",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address"},
                "first_name": {"type": "string", "description": "Contact first name"},
                "last_name": {"type": "string", "description": "Contact last name"},
                "user_id": {"type": "string", "description": "Your application's user ID for this contact"},
                "subscribed": {"type": "boolean", "description": "Whether the contact is subscribed to marketing", "default": True},
                "user_group": {"type": "string", "description": "User group/segment name"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "loops_update_contact",
        "description": "Update properties of an existing Loops contact by email",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address to update"},
                "first_name": {"type": "string", "description": "Updated first name"},
                "last_name": {"type": "string", "description": "Updated last name"},
                "user_id": {"type": "string", "description": "Updated application user ID"},
                "subscribed": {"type": "boolean", "description": "Updated subscription status"},
                "user_group": {"type": "string", "description": "Updated user group"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "loops_delete_contact",
        "description": "Delete a contact from Loops by email or user ID",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address"},
                "user_id": {"type": "string", "description": "Application user ID (alternative to email)"},
            },
        },
    },
    {
        "name": "loops_send_transactional_email",
        "description": "Send a transactional email using a Loops email template",
        "parameters": {
            "type": "object",
            "properties": {
                "transactional_id": {"type": "string", "description": "Loops transactional email template ID"},
                "email": {"type": "string", "description": "Recipient email address"},
                "data_variables": {"type": "object", "description": "Template variable key-value pairs"},
                "attachments": {"type": "array", "items": {"type": "object"}, "description": "List of attachment objects with filename and contentType"},
            },
            "required": ["transactional_id", "email"],
        },
    },
    {
        "name": "loops_find_contact",
        "description": "Search for a contact in Loops by email or user ID",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address to search for"},
                "user_id": {"type": "string", "description": "Application user ID to search for"},
            },
        },
    },
    {
        "name": "loops_list_events",
        "description": "List all custom event names available in the Loops account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("LOOPS_API_KEY"):
        return {"error": "LOOPS_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "loops_create_contact":
                payload: dict[str, Any] = {"email": arguments["email"]}
                for field in ("first_name", "last_name", "user_id", "subscribed", "user_group"):
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await client.post(f"{BASE_URL}/contacts/create", headers=_headers(), json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "loops_update_contact":
                payload = {"email": arguments["email"]}
                for field in ("first_name", "last_name", "user_id", "subscribed", "user_group"):
                    if field in arguments and field != "email":
                        payload[field] = arguments[field]
                r = await client.put(f"{BASE_URL}/contacts/update", headers=_headers(), json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "loops_delete_contact":
                payload = {}
                if "email" in arguments:
                    payload["email"] = arguments["email"]
                if "user_id" in arguments:
                    payload["userId"] = arguments["user_id"]
                if not payload:
                    return {"error": "Either email or user_id is required"}
                r = await client.post(f"{BASE_URL}/contacts/delete", headers=_headers(), json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "loops_send_transactional_email":
                payload = {
                    "transactionalId": arguments["transactional_id"],
                    "email": arguments["email"],
                }
                if "data_variables" in arguments:
                    payload["dataVariables"] = arguments["data_variables"]
                if "attachments" in arguments:
                    payload["attachments"] = arguments["attachments"]
                r = await client.post(f"{BASE_URL}/transactional", headers=_headers(), json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "loops_find_contact":
                params: dict[str, Any] = {}
                if "email" in arguments:
                    params["email"] = arguments["email"]
                if "user_id" in arguments:
                    params["userId"] = arguments["user_id"]
                if not params:
                    return {"error": "Either email or user_id is required"}
                r = await client.get(f"{BASE_URL}/contacts/find", headers=_headers(), params=params)
                r.raise_for_status()
                return {"contacts": r.json()}

            elif tool_name == "loops_list_events":
                r = await client.get(f"{BASE_URL}/events/event-names", headers=_headers())
                r.raise_for_status()
                return {"events": r.json()}

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("loops_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
