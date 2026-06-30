"""Emarsys MCP server — marketing platform with contacts, campaigns, and analytics.

Environment:
  EMARSYS_USERNAME: Emarsys API username
  EMARSYS_SECRET: Emarsys API secret key
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.emarsys.net/api/v2"

TOOL_DEFINITIONS = [
    {
        "name": "emarsys_list_contacts",
        "description": "List contacts in the Emarsys contact database",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum contacts to return"},
                "offset": {"type": "integer", "description": "Pagination offset"},
            },
        },
    },
    {
        "name": "emarsys_create_contact",
        "description": "Create a new contact in the Emarsys database",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address"},
                "first_name": {"type": "string", "description": "First name"},
                "last_name": {"type": "string", "description": "Last name"},
                "custom_fields": {"type": "object", "description": "Custom field ID to value mappings"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "emarsys_update_contact_data",
        "description": "Update data fields for an existing Emarsys contact",
        "parameters": {
            "type": "object",
            "properties": {
                "key_id": {"type": "integer", "description": "Field ID to use as key (3=email)"},
                "key_value": {"type": "string", "description": "Value of the key field"},
                "fields": {"type": "object", "description": "Field ID to value pairs to update"},
            },
            "required": ["key_id", "key_value", "fields"],
        },
    },
    {
        "name": "emarsys_list_campaigns",
        "description": "List email campaigns in Emarsys",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "integer", "description": "Campaign status filter (1=launched, 2=scheduled)"},
                "limit": {"type": "integer", "description": "Maximum campaigns"},
                "offset": {"type": "integer", "description": "Pagination offset"},
            },
        },
    },
    {
        "name": "emarsys_launch_campaign",
        "description": "Launch or schedule an Emarsys email campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID to launch"},
                "schedule": {"type": "string", "description": "ISO datetime to schedule launch (omit for immediate)"},
                "segment_id": {"type": "integer", "description": "Target segment/contact list ID"},
            },
            "required": ["campaign_id"],
        },
    },
    {
        "name": "emarsys_get_response_stats",
        "description": "Get email response statistics for a launched campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID"},
                "launch_id": {"type": "integer", "description": "Launch ID for specific send"},
            },
            "required": ["campaign_id"],
        },
    },
]


def _wsse_header(username: str, secret: str) -> str:
    nonce = secrets.token_hex(16)
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    digest = hashlib.sha1(f"{nonce}{created}{secret}".encode()).hexdigest()
    return (
        f'UsernameToken Username="{username}", '
        f'PasswordDigest="{digest}", '
        f'Nonce="{nonce}", '
        f'Created="{created}"'
    )


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    username = os.getenv("EMARSYS_USERNAME", "")
    secret = os.getenv("EMARSYS_SECRET", "")
    if not username or not secret:
        return {"error": "EMARSYS_USERNAME and EMARSYS_SECRET not configured"}

    headers = {
        "X-WSSE": _wsse_header(username, secret),
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "emarsys_list_contacts":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/contact/query/return=3", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "emarsys_create_contact":
                fields: dict[str, Any] = {"3": arguments["email"]}
                if "first_name" in arguments:
                    fields["1"] = arguments["first_name"]
                if "last_name" in arguments:
                    fields["2"] = arguments["last_name"]
                if "custom_fields" in arguments:
                    fields.update(arguments["custom_fields"])
                r = await client.post(f"{BASE_URL}/contact", headers=headers, json={"key_id": "3", "contacts": [fields]})
                r.raise_for_status()
                return r.json()

            if tool_name == "emarsys_update_contact_data":
                r = await client.put(
                    f"{BASE_URL}/contact",
                    headers=headers,
                    json={
                        "key_id": str(arguments["key_id"]),
                        "contacts": [{**{str(arguments["key_id"]): arguments["key_value"]}, **{str(k): v for k, v in arguments["fields"].items()}}],
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "emarsys_list_campaigns":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/email", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "emarsys_launch_campaign":
                payload: dict[str, Any] = {}
                if "schedule" in arguments:
                    payload["schedule"] = arguments["schedule"]
                if "segment_id" in arguments:
                    payload["segment_id"] = arguments["segment_id"]
                r = await client.post(
                    f"{BASE_URL}/email/{arguments['campaign_id']}/launch",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "emarsys_get_response_stats":
                campaign_id = arguments["campaign_id"]
                params: dict[str, Any] = {}
                if "launch_id" in arguments:
                    params["launch_id"] = arguments["launch_id"]
                r = await client.get(
                    f"{BASE_URL}/email/{campaign_id}/responsesummary",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
