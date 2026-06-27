"""Twilio MCP server — SMS, WhatsApp, voice calls, and number lookup.

Environment:
  TWILIO_ACCOUNT_SID: Account SID
  TWILIO_AUTH_TOKEN: Auth token
  TWILIO_FROM_NUMBER: Default from number (E.164 format, e.g. +15551234567)
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TWILIO_BASE = "https://api.twilio.com/2010-04-01"
LOOKUP_BASE = "https://lookups.twilio.com/v1"


def _auth() -> tuple[str, str]:
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    tok = os.getenv("TWILIO_AUTH_TOKEN", "")
    return (sid, tok)


TOOL_DEFINITIONS = [
    {
        "name": "twilio_send_sms",
        "description": "Send an SMS message via Twilio",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient phone number (E.164)"},
                "body": {"type": "string", "description": "SMS body text"},
                "from_number": {"type": "string", "description": "Sender number (uses TWILIO_FROM_NUMBER if omitted)"},
                "media_url": {"type": "string", "description": "URL of media attachment (MMS)"},
            },
            "required": ["to", "body"],
        },
    },
    {
        "name": "twilio_send_whatsapp",
        "description": "Send a WhatsApp message via Twilio",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient number (E.164 without whatsapp: prefix)"},
                "body": {"type": "string"},
                "from_number": {"type": "string", "description": "Twilio WhatsApp sender number"},
            },
            "required": ["to", "body"],
        },
    },
    {
        "name": "twilio_list_messages",
        "description": "List sent/received Twilio messages",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "default": 20},
                "to": {"type": "string", "description": "Filter by recipient number"},
                "from_number": {"type": "string", "description": "Filter by sender number"},
            },
        },
    },
    {
        "name": "twilio_make_call",
        "description": "Initiate an outbound phone call via Twilio",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Destination phone number (E.164)"},
                "twiml": {"type": "string", "description": "TwiML instructions for the call"},
                "from_number": {"type": "string"},
            },
            "required": ["to", "twiml"],
        },
    },
    {
        "name": "twilio_lookup_number",
        "description": "Look up carrier and line-type info for a phone number",
        "parameters": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string", "description": "Phone number in E.164 format"},
                "type": {
                    "type": "string",
                    "enum": ["carrier", "caller-name"],
                    "description": "Lookup type",
                },
            },
            "required": ["phone_number"],
        },
    },
    {
        "name": "twilio_list_numbers",
        "description": "List Twilio phone numbers owned by the account",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "default": 20},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    if not sid or not os.getenv("TWILIO_AUTH_TOKEN"):
        return {"error": "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN required"}

    from_default = os.getenv("TWILIO_FROM_NUMBER", "")

    try:
        if tool_name == "twilio_lookup_number":
            async with httpx.AsyncClient(auth=_auth(), timeout=30.0) as c:
                params: dict[str, Any] = {}
                if "type" in arguments:
                    params["Type"] = arguments["type"]
                r = await c.get(
                    f"{LOOKUP_BASE}/PhoneNumbers/{arguments['phone_number']}",
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "phone_number": data.get("phone_number"),
                    "national_format": data.get("national_format"),
                    "country_code": data.get("country_code"),
                    "carrier": data.get("carrier"),
                    "caller_name": data.get("caller_name"),
                }

        async with httpx.AsyncClient(auth=_auth(), timeout=30.0) as c:
            base = f"{TWILIO_BASE}/Accounts/{sid}"

            if tool_name == "twilio_send_sms":
                from_num = arguments.get("from_number") or from_default
                data: dict[str, Any] = {
                    "To": arguments["to"],
                    "From": from_num,
                    "Body": arguments["body"],
                }
                if "media_url" in arguments:
                    data["MediaUrl"] = arguments["media_url"]
                r = await c.post(f"{base}/Messages.json", data=data)
                r.raise_for_status()
                result = r.json()
                return {
                    "sid": result.get("sid"),
                    "status": result.get("status"),
                    "to": result.get("to"),
                }

            elif tool_name == "twilio_send_whatsapp":
                from_num = arguments.get("from_number") or from_default
                data = {
                    "To": f"whatsapp:{arguments['to']}",
                    "From": f"whatsapp:{from_num}",
                    "Body": arguments["body"],
                }
                r = await c.post(f"{base}/Messages.json", data=data)
                r.raise_for_status()
                result = r.json()
                return {"sid": result.get("sid"), "status": result.get("status")}

            elif tool_name == "twilio_list_messages":
                params = {"PageSize": arguments.get("page_size", 20)}
                if "to" in arguments:
                    params["To"] = arguments["to"]
                if "from_number" in arguments:
                    params["From"] = arguments["from_number"]
                r = await c.get(f"{base}/Messages.json", params=params)
                r.raise_for_status()
                result = r.json()
                return {
                    "messages": [
                        {
                            "sid": m.get("sid"),
                            "to": m.get("to"),
                            "from": m.get("from"),
                            "body": (m.get("body") or "")[:200],
                            "status": m.get("status"),
                            "date_sent": m.get("date_sent"),
                        }
                        for m in result.get("messages", [])
                    ]
                }

            elif tool_name == "twilio_make_call":
                from_num = arguments.get("from_number") or from_default
                data = {
                    "To": arguments["to"],
                    "From": from_num,
                    "Twiml": arguments["twiml"],
                }
                r = await c.post(f"{base}/Calls.json", data=data)
                r.raise_for_status()
                result = r.json()
                return {
                    "sid": result.get("sid"),
                    "status": result.get("status"),
                    "to": result.get("to"),
                }

            elif tool_name == "twilio_list_numbers":
                r = await c.get(
                    f"{base}/IncomingPhoneNumbers.json",
                    params={"PageSize": arguments.get("page_size", 20)},
                )
                r.raise_for_status()
                result = r.json()
                return {
                    "phone_numbers": [
                        {
                            "sid": n.get("sid"),
                            "phone_number": n.get("phone_number"),
                            "friendly_name": n.get("friendly_name"),
                        }
                        for n in result.get("incoming_phone_numbers", [])
                    ]
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("twilio_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
