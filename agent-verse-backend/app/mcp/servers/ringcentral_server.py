"""RingCentral MCP server — cloud communications: SMS, voice calls, and message management.

Environment:
  RINGCENTRAL_ACCESS_TOKEN: RingCentral OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://platform.ringcentral.com/restapi/v1.0"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('RINGCENTRAL_ACCESS_TOKEN', '')}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "ringcentral_send_sms",
        "description": "Send an SMS message via RingCentral",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "RingCentral account ID (use ~ for current account)", "default": "~"},
                "extension_id": {"type": "string", "description": "Extension ID to send from (use ~ for current)", "default": "~"},
                "to": {"type": "string", "description": "Recipient phone number in E.164 format"},
                "from_number": {"type": "string", "description": "Sender phone number (must be enabled for SMS)"},
                "text": {"type": "string", "description": "SMS message body"},
            },
            "required": ["to", "from_number", "text"],
        },
    },
    {
        "name": "ringcentral_list_messages",
        "description": "List messages in a RingCentral extension message store",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Account ID", "default": "~"},
                "extension_id": {"type": "string", "description": "Extension ID", "default": "~"},
                "message_type": {"type": "string", "description": "Filter by type: SMS, Fax, VoiceMail, InboundFax"},
                "per_page": {"type": "integer", "description": "Messages per page", "default": 100},
            },
        },
    },
    {
        "name": "ringcentral_make_call",
        "description": "Initiate an outbound phone call via RingCentral (ringout)",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Account ID", "default": "~"},
                "extension_id": {"type": "string", "description": "Extension ID", "default": "~"},
                "to": {"type": "string", "description": "Phone number to dial"},
                "from_number": {"type": "string", "description": "Caller ID phone number"},
                "play_prompt": {"type": "boolean", "description": "Play a connecting prompt before bridging", "default": True},
            },
            "required": ["to", "from_number"],
        },
    },
    {
        "name": "ringcentral_list_calls",
        "description": "List call log records for an account or extension",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Account ID", "default": "~"},
                "extension_id": {"type": "string", "description": "Extension ID", "default": "~"},
                "direction": {"type": "string", "description": "Filter by direction: Inbound, Outbound"},
                "type": {"type": "string", "description": "Filter by call type: Voice, Fax"},
                "per_page": {"type": "integer", "description": "Records per page", "default": 100},
            },
        },
    },
    {
        "name": "ringcentral_get_account_info",
        "description": "Retrieve information about a RingCentral account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Account ID (use ~ for current)", "default": "~"},
            },
        },
    },
    {
        "name": "ringcentral_list_extensions",
        "description": "List extensions registered under a RingCentral account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Account ID", "default": "~"},
                "extension_type": {"type": "string", "description": "Filter by type: User, Department, Announcement, etc."},
                "per_page": {"type": "integer", "description": "Extensions per page", "default": 100},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("RINGCENTRAL_ACCESS_TOKEN"):
        return {"error": "RINGCENTRAL_ACCESS_TOKEN not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            account_id = arguments.get("account_id", "~")
            extension_id = arguments.get("extension_id", "~")

            if tool_name == "ringcentral_send_sms":
                r = await client.post(
                    f"{BASE_URL}/account/{account_id}/extension/{extension_id}/sms",
                    headers=_headers(),
                    json={
                        "to": [{"phoneNumber": arguments["to"]}],
                        "from": {"phoneNumber": arguments["from_number"]},
                        "text": arguments["text"],
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "ringcentral_list_messages":
                params: dict[str, Any] = {"perPage": arguments.get("per_page", 100)}
                if "message_type" in arguments:
                    params["messageType"] = arguments["message_type"]
                r = await client.get(
                    f"{BASE_URL}/account/{account_id}/extension/{extension_id}/message-store",
                    headers=_headers(),
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "messages": [
                        {"id": m.get("id"), "type": m.get("type"), "direction": m.get("direction"), "status": m.get("messageStatus")}
                        for m in data.get("records", [])
                    ],
                    "paging": data.get("paging", {}),
                }

            elif tool_name == "ringcentral_make_call":
                r = await client.post(
                    f"{BASE_URL}/account/{account_id}/extension/{extension_id}/ringout",
                    headers=_headers(),
                    json={
                        "to": {"phoneNumber": arguments["to"]},
                        "from": {"phoneNumber": arguments["from_number"]},
                        "playPrompt": arguments.get("play_prompt", True),
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "ringcentral_list_calls":
                params = {"perPage": arguments.get("per_page", 100)}
                if "direction" in arguments:
                    params["direction"] = arguments["direction"]
                if "type" in arguments:
                    params["type"] = arguments["type"]
                r = await client.get(
                    f"{BASE_URL}/account/{account_id}/extension/{extension_id}/call-log",
                    headers=_headers(),
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "records": [
                        {"id": c.get("id"), "direction": c.get("direction"), "duration": c.get("duration"), "startTime": c.get("startTime")}
                        for c in data.get("records", [])
                    ]
                }

            elif tool_name == "ringcentral_get_account_info":
                r = await client.get(
                    f"{BASE_URL}/account/{account_id}",
                    headers=_headers(),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "ringcentral_list_extensions":
                params = {"perPage": arguments.get("per_page", 100)}
                if "extension_type" in arguments:
                    params["type"] = arguments["extension_type"]
                r = await client.get(
                    f"{BASE_URL}/account/{account_id}/extension",
                    headers=_headers(),
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "extensions": [
                        {"id": e.get("id"), "name": e.get("name"), "type": e.get("type"), "extensionNumber": e.get("extensionNumber")}
                        for e in data.get("records", [])
                    ]
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("ringcentral_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
