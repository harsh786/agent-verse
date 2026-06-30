"""Buffer MCP server — Buffer social media post scheduling, analytics, and profile management.

Environment:
  BUFFER_ACCESS_TOKEN: Buffer OAuth2 access token from developer portal
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.bufferapp.com/1"

TOOL_DEFINITIONS = [
    {
        "name": "buffer_list_profiles",
        "description": "List all connected social media profiles in the Buffer account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "buffer_create_update",
        "description": "Create a new post update in Buffer for scheduling",
        "parameters": {
            "type": "object",
            "properties": {
                "profile_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Buffer profile IDs to post to",
                },
                "text": {"type": "string", "description": "Post text content"},
                "scheduled_at": {"type": "string", "description": "ISO 8601 datetime to schedule the post"},
                "media": {
                    "type": "object",
                    "properties": {
                        "link": {"type": "string"},
                        "photo": {"type": "string"},
                        "description": {"type": "string"},
                        "title": {"type": "string"},
                    },
                    "description": "Media attachment for the post",
                },
                "now": {"type": "boolean", "description": "Post immediately without scheduling", "default": False},
            },
            "required": ["profile_ids", "text"],
        },
    },
    {
        "name": "buffer_list_updates",
        "description": "List pending (scheduled) updates for a Buffer profile",
        "parameters": {
            "type": "object",
            "properties": {
                "profile_id": {"type": "string", "description": "Buffer profile ID"},
                "page": {"type": "integer", "description": "Page number for pagination", "default": 1},
                "count": {"type": "integer", "description": "Number of updates per page", "default": 20},
                "utc": {"type": "boolean", "description": "Return times in UTC", "default": True},
            },
            "required": ["profile_id"],
        },
    },
    {
        "name": "buffer_get_profile_analytics",
        "description": "Get analytics and statistics for a Buffer social profile",
        "parameters": {
            "type": "object",
            "properties": {
                "profile_id": {"type": "string", "description": "Buffer profile ID"},
            },
            "required": ["profile_id"],
        },
    },
    {
        "name": "buffer_list_sent_updates",
        "description": "List already-sent updates for a Buffer profile",
        "parameters": {
            "type": "object",
            "properties": {
                "profile_id": {"type": "string", "description": "Buffer profile ID"},
                "page": {"type": "integer", "description": "Page number", "default": 1},
                "count": {"type": "integer", "description": "Number of sent updates per page", "default": 20},
                "filter": {"type": "string", "description": "Filter type: engagement"},
            },
            "required": ["profile_id"],
        },
    },
    {
        "name": "buffer_reorder_updates",
        "description": "Reorder pending updates in a Buffer profile queue",
        "parameters": {
            "type": "object",
            "properties": {
                "profile_id": {"type": "string", "description": "Buffer profile ID"},
                "order": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered list of update IDs defining the new queue order",
                },
                "offset": {"type": "integer", "description": "Position offset to start reordering at", "default": 0},
                "utc": {"type": "boolean", "description": "Times in UTC", "default": True},
            },
            "required": ["profile_id", "order"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("BUFFER_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "BUFFER_ACCESS_TOKEN not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "buffer_list_profiles":
                r = await client.get(
                    f"{BASE}/profiles.json",
                    params={"access_token": access_token},
                )
                r.raise_for_status()
                profiles = r.json()
                if not isinstance(profiles, list):
                    profiles = []
                return {
                    "profiles": [
                        {
                            "id": p.get("id"),
                            "service": p.get("service"),
                            "username": p.get("service_username"),
                            "formatted_username": p.get("formatted_username"),
                        }
                        for p in profiles
                    ],
                }

            elif tool_name == "buffer_create_update":
                payload: dict[str, Any] = {
                    "text": arguments["text"],
                    "access_token": access_token,
                }
                for pid in arguments["profile_ids"]:
                    payload[f"profile_ids[]"] = pid
                if "scheduled_at" in arguments:
                    payload["scheduled_at"] = arguments["scheduled_at"]
                if arguments.get("now"):
                    payload["now"] = "true"
                if "media" in arguments:
                    for k, v in arguments["media"].items():
                        payload[f"media[{k}]"] = v
                r = await client.post(
                    f"{BASE}/updates/create.json",
                    data={k: (v if not isinstance(v, bool) else str(v).lower()) for k, v in payload.items()},
                )
                r.raise_for_status()
                data = r.json()
                updates = data.get("updates", [])
                return {
                    "created": len(updates),
                    "ids": [u.get("id") for u in updates],
                    "status": data.get("success"),
                }

            elif tool_name == "buffer_list_updates":
                params: dict[str, Any] = {
                    "access_token": access_token,
                    "page": arguments.get("page", 1),
                    "count": arguments.get("count", 20),
                    "utc": "true" if arguments.get("utc", True) else "false",
                }
                r = await client.get(
                    f"{BASE}/profiles/{arguments['profile_id']}/updates/pending.json",
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "updates": [
                        {
                            "id": u.get("id"),
                            "text": u.get("text"),
                            "status": u.get("status"),
                            "scheduled_at": u.get("scheduled_at"),
                        }
                        for u in data.get("updates", [])
                    ],
                    "total": data.get("total", 0),
                }

            elif tool_name == "buffer_get_profile_analytics":
                r = await client.get(
                    f"{BASE}/profiles/{arguments['profile_id']}.json",
                    params={"access_token": access_token},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "service": data.get("service"),
                    "username": data.get("service_username"),
                    "statistics": data.get("statistics", {}),
                }

            elif tool_name == "buffer_list_sent_updates":
                params = {
                    "access_token": access_token,
                    "page": arguments.get("page", 1),
                    "count": arguments.get("count", 20),
                }
                if "filter" in arguments:
                    params["filter"] = arguments["filter"]
                r = await client.get(
                    f"{BASE}/profiles/{arguments['profile_id']}/updates/sent.json",
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "updates": data.get("updates", []),
                    "total": data.get("total", 0),
                }

            elif tool_name == "buffer_reorder_updates":
                payload = {
                    "access_token": access_token,
                    "offset": arguments.get("offset", 0),
                    "utc": "true" if arguments.get("utc", True) else "false",
                }
                for oid in arguments["order"]:
                    payload["order[]"] = oid
                r = await client.post(
                    f"{BASE}/profiles/{arguments['profile_id']}/updates/reorder.json",
                    data=payload,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("buffer_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
