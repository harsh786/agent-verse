"""Plivo MCP server — cloud communications: SMS, voice calls, and phone number management.

Environment:
  PLIVO_AUTH_ID: Plivo Auth ID from console.plivo.com
  PLIVO_AUTH_TOKEN: Plivo Auth Token from console.plivo.com
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.plivo.com/v1"


def _auth() -> tuple[str, str]:
    return (os.getenv("PLIVO_AUTH_ID", ""), os.getenv("PLIVO_AUTH_TOKEN", ""))


TOOL_DEFINITIONS = [
    {
        "name": "plivo_send_sms",
        "description": "Send an SMS message via Plivo",
        "parameters": {
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "Sender phone number or alphanumeric sender ID"},
                "dst": {"type": "string", "description": "Recipient phone number(s) in E.164 format, comma-separated"},
                "text": {"type": "string", "description": "SMS message body"},
                "url": {"type": "string", "description": "Callback URL for delivery status"},
                "method": {"type": "string", "description": "HTTP method for callback: GET or POST", "default": "POST"},
            },
            "required": ["src", "dst", "text"],
        },
    },
    {
        "name": "plivo_list_messages",
        "description": "List SMS messages sent or received on the Plivo account",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max messages to return (max 20)", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
                "message_direction": {"type": "string", "description": "Filter by direction: inbound, outbound"},
                "message_state": {"type": "string", "description": "Filter by state: queued, sent, failed, delivered, undelivered"},
            },
        },
    },
    {
        "name": "plivo_make_call",
        "description": "Initiate an outbound voice call via Plivo",
        "parameters": {
            "type": "object",
            "properties": {
                "from_number": {"type": "string", "description": "Caller ID phone number (must be a Plivo number)"},
                "to_number": {"type": "string", "description": "Destination phone number in E.164 format"},
                "answer_url": {"type": "string", "description": "URL returning a Plivo XML response to control the call"},
                "answer_method": {"type": "string", "description": "HTTP method for answer_url: GET or POST", "default": "POST"},
            },
            "required": ["from_number", "to_number", "answer_url"],
        },
    },
    {
        "name": "plivo_list_calls",
        "description": "List call detail records for the Plivo account",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max records to return (max 20)", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
                "call_direction": {"type": "string", "description": "Filter by direction: inbound, outbound"},
            },
        },
    },
    {
        "name": "plivo_get_account",
        "description": "Retrieve account details and current balance for the Plivo account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "plivo_list_phone_numbers",
        "description": "List all phone numbers rented on the Plivo account",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max numbers to return", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
                "type": {"type": "string", "description": "Filter by type: local, tollfree, mobile"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    auth_id = os.getenv("PLIVO_AUTH_ID", "")
    auth_token = os.getenv("PLIVO_AUTH_TOKEN", "")
    if not auth_id:
        return {"error": "PLIVO_AUTH_ID not configured"}
    if not auth_token:
        return {"error": "PLIVO_AUTH_TOKEN not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "plivo_send_sms":
                payload: dict[str, Any] = {
                    "src": arguments["src"],
                    "dst": arguments["dst"],
                    "text": arguments["text"],
                }
                if "url" in arguments:
                    payload["url"] = arguments["url"]
                if "method" in arguments:
                    payload["method"] = arguments["method"]
                r = await client.post(
                    f"{BASE_URL}/Account/{auth_id}/Message/",
                    auth=_auth(),
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "plivo_list_messages":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "message_direction" in arguments:
                    params["message_direction"] = arguments["message_direction"]
                if "message_state" in arguments:
                    params["message_state"] = arguments["message_state"]
                r = await client.get(
                    f"{BASE_URL}/Account/{auth_id}/Message/",
                    auth=_auth(),
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "messages": [
                        {"message_uuid": m.get("message_uuid"), "from_number": m.get("from_number"), "to_number": m.get("to_number"), "message_state": m.get("message_state")}
                        for m in data.get("objects", [])
                    ],
                    "meta": data.get("meta", {}),
                }

            elif tool_name == "plivo_make_call":
                r = await client.post(
                    f"{BASE_URL}/Account/{auth_id}/Call/",
                    auth=_auth(),
                    json={
                        "from": arguments["from_number"],
                        "to": arguments["to_number"],
                        "answer_url": arguments["answer_url"],
                        "answer_method": arguments.get("answer_method", "POST"),
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "plivo_list_calls":
                params = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "call_direction" in arguments:
                    params["call_direction"] = arguments["call_direction"]
                r = await client.get(
                    f"{BASE_URL}/Account/{auth_id}/Call/",
                    auth=_auth(),
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "calls": [
                        {"call_uuid": c.get("call_uuid"), "from_number": c.get("from_number"), "to_number": c.get("to_number"), "duration": c.get("duration")}
                        for c in data.get("objects", [])
                    ]
                }

            elif tool_name == "plivo_get_account":
                r = await client.get(
                    f"{BASE_URL}/Account/{auth_id}/",
                    auth=_auth(),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "plivo_list_phone_numbers":
                params = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "type" in arguments:
                    params["type"] = arguments["type"]
                r = await client.get(
                    f"{BASE_URL}/Account/{auth_id}/Number/",
                    auth=_auth(),
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "numbers": [
                        {"number": n.get("number"), "type": n.get("number_type"), "country": n.get("country")}
                        for n in data.get("objects", [])
                    ]
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("plivo_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
