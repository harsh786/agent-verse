"""Vonage (Nexmo) MCP server — SMS messaging, voice calls, and account management.

Environment:
  VONAGE_API_KEY: Vonage API key from dashboard.nexmo.com
  VONAGE_API_SECRET: Vonage API secret from dashboard.nexmo.com
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://rest.nexmo.com"


TOOL_DEFINITIONS = [
    {
        "name": "vonage_send_sms",
        "description": "Send an SMS message via Vonage (Nexmo)",
        "parameters": {
            "type": "object",
            "properties": {
                "from_number": {"type": "string", "description": "Sender number or alphanumeric ID"},
                "to": {"type": "string", "description": "Recipient phone number in E.164 format"},
                "text": {"type": "string", "description": "SMS message body"},
                "type": {"type": "string", "description": "Message type: text, binary, wappush, unicode", "default": "text"},
            },
            "required": ["from_number", "to", "text"],
        },
    },
    {
        "name": "vonage_get_message",
        "description": "Retrieve details of a specific Vonage message by message ID",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Vonage message ID"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "vonage_send_voice_call",
        "description": "Initiate an outbound voice call via the Vonage Voice API",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Destination phone number in E.164 format"},
                "from_number": {"type": "string", "description": "Vonage virtual number to call from"},
                "answer_url": {"type": "string", "description": "URL to fetch the NCCO (Nexmo Call Control Object)"},
                "answer_method": {"type": "string", "description": "HTTP method for answer_url: GET or POST", "default": "GET"},
            },
            "required": ["to", "from_number", "answer_url"],
        },
    },
    {
        "name": "vonage_list_calls",
        "description": "List call detail records on the Vonage account",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by call status: started, ringing, answered, machine, completed, busy, cancelled, failed, rejected, timeout, unanswered"},
                "date_start": {"type": "string", "description": "Filter calls starting after this ISO 8601 datetime"},
                "date_end": {"type": "string", "description": "Filter calls ending before this ISO 8601 datetime"},
                "page_size": {"type": "integer", "description": "Records per page (max 100)", "default": 10},
            },
        },
    },
    {
        "name": "vonage_get_account_balance",
        "description": "Retrieve the current account balance for the Vonage account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "vonage_list_numbers",
        "description": "List virtual phone numbers rented on the Vonage account",
        "parameters": {
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "Two-letter ISO 3166-1 country code filter"},
                "pattern": {"type": "string", "description": "Phone number pattern filter"},
                "index": {"type": "integer", "description": "Page index (1-based)", "default": 1},
                "size": {"type": "integer", "description": "Numbers per page", "default": 10},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("VONAGE_API_KEY", "")
    api_secret = os.getenv("VONAGE_API_SECRET", "")
    if not api_key:
        return {"error": "VONAGE_API_KEY not configured"}
    if not api_secret:
        return {"error": "VONAGE_API_SECRET not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "vonage_send_sms":
                r = await client.post(
                    f"{BASE_URL}/sms/json",
                    data={
                        "api_key": api_key,
                        "api_secret": api_secret,
                        "from": arguments["from_number"],
                        "to": arguments["to"],
                        "text": arguments["text"],
                        "type": arguments.get("type", "text"),
                    },
                )
                r.raise_for_status()
                data = r.json()
                messages = data.get("messages", [])
                return {
                    "messages": [
                        {"message-id": m.get("message-id"), "status": m.get("status"), "to": m.get("to")}
                        for m in messages
                    ],
                    "message-count": data.get("message-count"),
                }

            elif tool_name == "vonage_get_message":
                r = await client.get(
                    f"{BASE_URL}/search/message",
                    params={
                        "api_key": api_key,
                        "api_secret": api_secret,
                        "id": arguments["message_id"],
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "vonage_send_voice_call":
                # Voice API uses a different base URL and Bearer JWT auth; using basic auth here
                r = await client.post(
                    "https://api.nexmo.com/v1/calls",
                    headers={"Content-Type": "application/json"},
                    auth=(api_key, api_secret),
                    json={
                        "to": [{"type": "phone", "number": arguments["to"]}],
                        "from": {"type": "phone", "number": arguments["from_number"]},
                        "answer_url": [arguments["answer_url"]],
                        "answer_method": arguments.get("answer_method", "GET"),
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "vonage_list_calls":
                params: dict[str, Any] = {
                    "api_key": api_key,
                    "api_secret": api_secret,
                    "page_size": arguments.get("page_size", 10),
                }
                if "status" in arguments:
                    params["status"] = arguments["status"]
                if "date_start" in arguments:
                    params["date_start"] = arguments["date_start"]
                if "date_end" in arguments:
                    params["date_end"] = arguments["date_end"]
                r = await client.get("https://api.nexmo.com/v1/calls", params=params)
                r.raise_for_status()
                data = r.json()
                embedded = data.get("_embedded", {})
                return {
                    "calls": [
                        {"uuid": c.get("uuid"), "status": c.get("status"), "direction": c.get("direction"), "duration": c.get("duration")}
                        for c in embedded.get("calls", [])
                    ],
                    "count": data.get("count", 0),
                }

            elif tool_name == "vonage_get_account_balance":
                r = await client.get(
                    f"{BASE_URL}/account/get-balance",
                    params={"api_key": api_key, "api_secret": api_secret},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "vonage_list_numbers":
                params = {
                    "api_key": api_key,
                    "api_secret": api_secret,
                    "index": arguments.get("index", 1),
                    "size": arguments.get("size", 10),
                }
                if "country" in arguments:
                    params["country"] = arguments["country"]
                if "pattern" in arguments:
                    params["pattern"] = arguments["pattern"]
                r = await client.get(
                    f"{BASE_URL}/account/numbers",
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "numbers": [
                        {"msisdn": n.get("msisdn"), "country": n.get("country"), "type": n.get("type")}
                        for n in data.get("numbers", [])
                    ],
                    "count": data.get("count", 0),
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("vonage_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
