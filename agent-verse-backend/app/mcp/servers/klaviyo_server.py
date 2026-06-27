"""Klaviyo MCP server — profiles, lists, events, and campaigns.

Environment:
  KLAVIYO_API_KEY: Private API key (starts with pk_)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

KLAVIYO_BASE = "https://a.klaviyo.com/api"
KLAVIYO_REVISION = "2024-02-15"

TOOL_DEFINITIONS = [
    {
        "name": "klaviyo_list_profiles",
        "description": "List Klaviyo profiles/contacts",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "default": 20},
                "filter": {
                    "type": "string",
                    "description": "Filter expression, e.g. equals(email,'test@example.com')",
                },
            },
        },
    },
    {
        "name": "klaviyo_create_profile",
        "description": "Create a new Klaviyo profile",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "phone_number": {"type": "string"},
                "properties": {"type": "object", "description": "Custom profile properties"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "klaviyo_update_profile",
        "description": "Update an existing Klaviyo profile by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "profile_id": {"type": "string"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "phone_number": {"type": "string"},
                "properties": {"type": "object"},
            },
            "required": ["profile_id"],
        },
    },
    {
        "name": "klaviyo_list_lists",
        "description": "List all Klaviyo lists",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "klaviyo_add_to_list",
        "description": "Add profiles to a Klaviyo list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string"},
                "profile_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Klaviyo profile IDs to add",
                },
            },
            "required": ["list_id", "profile_ids"],
        },
    },
    {
        "name": "klaviyo_send_event",
        "description": "Track a custom event for a profile",
        "parameters": {
            "type": "object",
            "properties": {
                "event_name": {"type": "string", "description": "Event metric name, e.g. 'Placed Order'"},
                "profile_email": {"type": "string"},
                "properties": {"type": "object", "description": "Event properties"},
                "value": {"type": "number", "description": "Optional monetary value"},
            },
            "required": ["event_name", "profile_email"],
        },
    },
    {
        "name": "klaviyo_list_campaigns",
        "description": "List Klaviyo email campaigns",
        "parameters": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Filter, e.g. equals(messages.channel,'email')",
                    "default": "equals(messages.channel,'email')",
                },
                "page_size": {"type": "integer", "default": 20},
            },
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("KLAVIYO_API_KEY", "")
    return {
        "Authorization": f"Klaviyo-API-Key {key}",
        "Content-Type": "application/json",
        "revision": KLAVIYO_REVISION,
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("KLAVIYO_API_KEY"):
        return {"error": "KLAVIYO_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=KLAVIYO_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "klaviyo_list_profiles":
                params: dict[str, Any] = {
                    "page[size]": arguments.get("page_size", 20),
                }
                if "filter" in arguments:
                    params["filter"] = arguments["filter"]
                r = await c.get("/profiles/", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "profiles": [
                        {
                            "id": p["id"],
                            "email": p.get("attributes", {}).get("email", ""),
                            "first_name": p.get("attributes", {}).get("first_name", ""),
                        }
                        for p in data.get("data", [])
                    ]
                }

            elif tool_name == "klaviyo_create_profile":
                attrs: dict[str, Any] = {"email": arguments["email"]}
                for field in ("first_name", "last_name", "phone_number"):
                    if field in arguments:
                        attrs[field] = arguments[field]
                if "properties" in arguments:
                    attrs["properties"] = arguments["properties"]
                payload: dict[str, Any] = {
                    "data": {"type": "profile", "attributes": attrs}
                }
                r = await c.post("/profiles/", json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("data", {}).get("id"),
                    "email": data.get("data", {}).get("attributes", {}).get("email"),
                }

            elif tool_name == "klaviyo_update_profile":
                attrs = {}
                for field in ("first_name", "last_name", "phone_number"):
                    if field in arguments:
                        attrs[field] = arguments[field]
                if "properties" in arguments:
                    attrs["properties"] = arguments["properties"]
                payload = {
                    "data": {
                        "type": "profile",
                        "id": arguments["profile_id"],
                        "attributes": attrs,
                    }
                }
                r = await c.patch(f"/profiles/{arguments['profile_id']}/", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("data", {}).get("id")}

            elif tool_name == "klaviyo_list_lists":
                r = await c.get(
                    "/lists/",
                    params={"page[size]": arguments.get("page_size", 20)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "lists": [
                        {"id": lst["id"], "name": lst.get("attributes", {}).get("name", "")}
                        for lst in data.get("data", [])
                    ]
                }

            elif tool_name == "klaviyo_add_to_list":
                payload = {
                    "data": [
                        {"type": "profile", "id": pid}
                        for pid in arguments["profile_ids"]
                    ]
                }
                r = await c.post(
                    f"/lists/{arguments['list_id']}/relationships/profiles/", json=payload
                )
                return {"success": r.status_code in (200, 204), "status_code": r.status_code}

            elif tool_name == "klaviyo_send_event":
                payload = {
                    "data": {
                        "type": "event",
                        "attributes": {
                            "metric": {"data": {"type": "metric", "attributes": {"name": arguments["event_name"]}}},
                            "profile": {
                                "data": {
                                    "type": "profile",
                                    "attributes": {"email": arguments["profile_email"]},
                                }
                            },
                            "properties": arguments.get("properties", {}),
                        },
                    }
                }
                if "value" in arguments:
                    payload["data"]["attributes"]["value"] = arguments["value"]
                r = await c.post("/events/", json=payload)
                return {"success": r.status_code in (200, 202), "status_code": r.status_code}

            elif tool_name == "klaviyo_list_campaigns":
                params = {
                    "page[size]": arguments.get("page_size", 20),
                }
                filter_val = arguments.get("filter", "equals(messages.channel,'email')")
                if filter_val:
                    params["filter"] = filter_val
                r = await c.get("/campaigns/", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "campaigns": [
                        {
                            "id": camp["id"],
                            "name": camp.get("attributes", {}).get("name", ""),
                            "status": camp.get("attributes", {}).get("status", ""),
                        }
                        for camp in data.get("data", [])
                    ]
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("klaviyo_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
