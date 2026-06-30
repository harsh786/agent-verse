"""Vero MCP server — email & push: user identification, event tracking, and campaigns.

Environment variables:
  VERO_AUTH_TOKEN: Vero API authentication token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

VERO_BASE = "https://api.getvero.com/api/v2"

TOOL_DEFINITIONS = [
    {
        "name": "vero_identify_user",
        "description": "Identify or create a user in Vero with their attributes and traits",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Your unique user ID"},
                "email": {"type": "string", "description": "User's email address"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "channels": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Push notification channels, e.g. [{type: 'push', address: 'token', platform: 'ios'}]",
                },
                "data": {"type": "object", "description": "Additional custom attributes"},
            },
            "required": ["id", "email"],
        },
    },
    {
        "name": "vero_track_event",
        "description": "Track a custom event for a user in Vero to trigger automated campaigns",
        "parameters": {
            "type": "object",
            "properties": {
                "identity": {
                    "type": "object",
                    "description": "User identity — {id: '...', email: '...'}",
                },
                "event_name": {"type": "string", "description": "Event name, e.g. 'Purchased Product'"},
                "data": {"type": "object", "description": "Event-level data/properties"},
                "extras": {"type": "object", "description": "Extra metadata for the event"},
            },
            "required": ["identity", "event_name"],
        },
    },
    {
        "name": "vero_update_user",
        "description": "Update an existing Vero user's attributes",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "User ID"},
                "email": {"type": "string"},
                "changes": {
                    "type": "object",
                    "description": "Attribute key-value pairs to update",
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "vero_unsubscribe_user",
        "description": "Unsubscribe a user from Vero email communications",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "User ID to unsubscribe"},
                "email": {"type": "string", "description": "User's email address"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "vero_create_campaign",
        "description": "Create a new email or push campaign in Vero",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Email subject line"},
                "from_name": {"type": "string", "description": "Sender name"},
                "from_address": {"type": "string", "description": "Sender email address"},
                "reply_to": {"type": "string"},
                "trigger_event": {"type": "string", "description": "Event name that triggers the campaign"},
                "segment_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Segment IDs to target",
                },
            },
            "required": ["subject"],
        },
    },
    {
        "name": "vero_send_email",
        "description": "Send a transactional email to a specific user via Vero",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "object",
                    "description": "Recipient — {id: '...', email: '...'}",
                },
                "email_id": {"type": "integer", "description": "Vero email template ID"},
                "data": {"type": "object", "description": "Template variables for personalisation"},
            },
            "required": ["to", "email_id"],
        },
    },
]


def _headers(auth_token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    auth_token = os.getenv("VERO_AUTH_TOKEN", "")
    if not auth_token:
        return {"error": "VERO_AUTH_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=VERO_BASE,
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        ) as c:
            # All Vero API calls include auth_token in the body
            if tool_name == "vero_identify_user":
                body: dict[str, Any] = {
                    "auth_token": auth_token,
                    "id": arguments["id"],
                    "email": arguments["email"],
                }
                for k in ("first_name", "last_name", "channels"):
                    if k in arguments:
                        body[k] = arguments[k]
                if "data" in arguments:
                    body["data"] = arguments["data"]
                r = await c.post("/users/track", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "vero_track_event":
                body = {
                    "auth_token": auth_token,
                    "identity": arguments["identity"],
                    "event_name": arguments["event_name"],
                }
                for k in ("data", "extras"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/events/track", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "vero_update_user":
                body = {
                    "auth_token": auth_token,
                    "id": arguments["id"],
                }
                if "email" in arguments:
                    body["email"] = arguments["email"]
                if "changes" in arguments:
                    body["changes"] = arguments["changes"]
                r = await c.put("/users/edit", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "vero_unsubscribe_user":
                body = {
                    "auth_token": auth_token,
                    "id": arguments["id"],
                }
                if "email" in arguments:
                    body["email"] = arguments["email"]
                r = await c.post("/users/unsubscribe", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "vero_create_campaign":
                body = {
                    "auth_token": auth_token,
                    "subject": arguments["subject"],
                }
                for k in ("from_name", "from_address", "reply_to", "trigger_event", "segment_ids"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/campaigns", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "vero_send_email":
                body = {
                    "auth_token": auth_token,
                    "to": arguments["to"],
                    "email_id": arguments["email_id"],
                }
                if "data" in arguments:
                    body["data"] = arguments["data"]
                r = await c.post("/emails/send", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("vero_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
