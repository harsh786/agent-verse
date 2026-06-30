"""eSputnik MCP server — omnichannel marketing automation and contact management.

Environment:
  ESPUTNIK_LOGIN: eSputnik account login (email)
  ESPUTNIK_PASSWORD: eSputnik API password
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://esputnik.com/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "esputnik_add_contact",
        "description": "Add or update a contact in eSputnik",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address"},
                "first_name": {"type": "string", "description": "First name"},
                "last_name": {"type": "string", "description": "Last name"},
                "phone": {"type": "string", "description": "Phone number"},
                "fields": {
                    "type": "array",
                    "description": "Extra field objects with name and value",
                    "items": {"type": "object"},
                },
            },
            "required": ["email"],
        },
    },
    {
        "name": "esputnik_get_contact",
        "description": "Look up an eSputnik contact by email address",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Email to look up"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "esputnik_list_campaigns",
        "description": "List email or SMS campaigns in eSputnik",
        "parameters": {
            "type": "object",
            "properties": {
                "start_index": {"type": "integer", "description": "Pagination start index"},
                "max_rows": {"type": "integer", "description": "Maximum campaigns to return"},
                "type": {"type": "string", "description": "Campaign type: email, sms, push"},
            },
        },
    },
    {
        "name": "esputnik_send_email",
        "description": "Send a transactional email to a contact via eSputnik",
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "Recipient email address"},
                "params": {"type": "array", "description": "Template variables as name-value pairs", "items": {"type": "object"}},
                "template_id": {"type": "integer", "description": "Email template ID"},
            },
            "required": ["recipient"],
        },
    },
    {
        "name": "esputnik_track_event",
        "description": "Track a custom event for a contact in eSputnik for automation triggers",
        "parameters": {
            "type": "object",
            "properties": {
                "event_type_key": {"type": "string", "description": "Event type key identifier"},
                "key_value": {"type": "string", "description": "Contact identifier (email or phone)"},
                "params": {"type": "array", "description": "Event parameters as key-value pairs", "items": {"type": "object"}},
            },
            "required": ["event_type_key", "key_value"],
        },
    },
    {
        "name": "esputnik_list_segments",
        "description": "List audience segments defined in eSputnik",
        "parameters": {
            "type": "object",
            "properties": {
                "start_index": {"type": "integer", "description": "Pagination start"},
                "max_rows": {"type": "integer", "description": "Maximum segments"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    login = os.getenv("ESPUTNIK_LOGIN", "")
    password = os.getenv("ESPUTNIK_PASSWORD", "")
    if not login or not password:
        return {"error": "ESPUTNIK_LOGIN and ESPUTNIK_PASSWORD not configured"}

    auth = (login, password)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "esputnik_add_contact":
                channels = []
                if "email" in arguments:
                    channels.append({"type": "email", "value": arguments["email"]})
                if "phone" in arguments:
                    channels.append({"type": "sms", "value": arguments["phone"]})
                contact: dict[str, Any] = {"channels": channels}
                if "first_name" in arguments:
                    contact["firstName"] = arguments["first_name"]
                if "last_name" in arguments:
                    contact["lastName"] = arguments["last_name"]
                if "fields" in arguments:
                    contact["fields"] = arguments["fields"]
                r = await client.post(
                    f"{BASE_URL}/contact",
                    auth=auth,
                    headers=headers,
                    json={"contact": contact},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "esputnik_get_contact":
                r = await client.get(
                    f"{BASE_URL}/contact",
                    auth=auth,
                    params={"email": arguments["email"]},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "esputnik_list_campaigns":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/messages", auth=auth, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "esputnik_send_email":
                payload: dict[str, Any] = {
                    "recipients": [{"jsonParam": {"email": arguments["recipient"]}}],
                }
                if "params" in arguments:
                    payload["recipients"][0]["jsonParam"]["params"] = arguments["params"]
                if "template_id" in arguments:
                    payload["email"] = {"templateId": arguments["template_id"]}
                r = await client.post(f"{BASE_URL}/message/email", auth=auth, headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "esputnik_track_event":
                r = await client.post(
                    f"{BASE_URL}/event",
                    auth=auth,
                    headers=headers,
                    json={
                        "eventTypeKey": arguments["event_type_key"],
                        "keyValue": arguments["key_value"],
                        "params": arguments.get("params", []),
                    },
                )
                r.raise_for_status()
                return r.json() if r.content else {"tracked": True}

            if tool_name == "esputnik_list_segments":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/segments", auth=auth, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
