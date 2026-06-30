"""OneSignal MCP server — push notifications, device management, and segments.

Environment:
  ONESIGNAL_API_KEY: OneSignal REST API key from Settings > Keys & IDs
  ONESIGNAL_APP_ID: OneSignal application ID from Settings > Keys & IDs
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://onesignal.com/api/v1"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Basic {os.getenv('ONESIGNAL_API_KEY', '')}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "onesignal_create_notification",
        "description": "Create and send a push notification via OneSignal",
        "parameters": {
            "type": "object",
            "properties": {
                "headings": {"type": "object", "description": "Notification title by language, e.g. {\"en\": \"Hello\"}"},
                "contents": {"type": "object", "description": "Notification body by language, e.g. {\"en\": \"World\"}"},
                "included_segments": {"type": "array", "items": {"type": "string"}, "description": "Audience segments to target, e.g. [\"All\"]"},
                "include_player_ids": {"type": "array", "items": {"type": "string"}, "description": "Specific device player IDs to target"},
                "data": {"type": "object", "description": "Custom key-value data payload for the notification"},
                "url": {"type": "string", "description": "URL to open when notification is clicked"},
            },
            "required": ["contents"],
        },
    },
    {
        "name": "onesignal_list_devices",
        "description": "List registered devices/players for a OneSignal app",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max devices to return (max 300)", "default": 300},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
    {
        "name": "onesignal_get_notification",
        "description": "Retrieve details and delivery stats for a specific OneSignal notification",
        "parameters": {
            "type": "object",
            "properties": {
                "notification_id": {"type": "string", "description": "OneSignal notification ID"},
            },
            "required": ["notification_id"],
        },
    },
    {
        "name": "onesignal_cancel_notification",
        "description": "Cancel a scheduled OneSignal notification before it is sent",
        "parameters": {
            "type": "object",
            "properties": {
                "notification_id": {"type": "string", "description": "OneSignal notification ID to cancel"},
            },
            "required": ["notification_id"],
        },
    },
    {
        "name": "onesignal_view_apps",
        "description": "List all OneSignal apps accessible with the current API key",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "onesignal_create_segment",
        "description": "Create a new user segment in OneSignal for targeting notifications",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Segment name"},
                "filters": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Segment filter rules, e.g. [{\"field\": \"tag\", \"key\": \"level\", \"relation\": \">\", \"value\": \"10\"}]",
                },
            },
            "required": ["name", "filters"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("ONESIGNAL_API_KEY", "")
    app_id = os.getenv("ONESIGNAL_APP_ID", "")
    if not api_key:
        return {"error": "ONESIGNAL_API_KEY not configured"}
    if not app_id:
        return {"error": "ONESIGNAL_APP_ID not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "onesignal_create_notification":
                payload: dict[str, Any] = {
                    "app_id": app_id,
                    "contents": arguments["contents"],
                }
                if "headings" in arguments:
                    payload["headings"] = arguments["headings"]
                if "included_segments" in arguments:
                    payload["included_segments"] = arguments["included_segments"]
                if "include_player_ids" in arguments:
                    payload["include_player_ids"] = arguments["include_player_ids"]
                if "data" in arguments:
                    payload["data"] = arguments["data"]
                if "url" in arguments:
                    payload["url"] = arguments["url"]
                r = await client.post(f"{BASE_URL}/notifications", headers=_headers(), json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "onesignal_list_devices":
                r = await client.get(
                    f"{BASE_URL}/players",
                    headers=_headers(),
                    params={
                        "app_id": app_id,
                        "limit": arguments.get("limit", 300),
                        "offset": arguments.get("offset", 0),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "players": [
                        {"id": p.get("id"), "device_type": p.get("device_type"), "last_active": p.get("last_active")}
                        for p in data.get("players", [])
                    ],
                    "total_count": data.get("total_count", 0),
                }

            elif tool_name == "onesignal_get_notification":
                r = await client.get(
                    f"{BASE_URL}/notifications/{arguments['notification_id']}",
                    headers=_headers(),
                    params={"app_id": app_id},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "onesignal_cancel_notification":
                r = await client.delete(
                    f"{BASE_URL}/notifications/{arguments['notification_id']}",
                    headers=_headers(),
                    params={"app_id": app_id},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "onesignal_view_apps":
                r = await client.get(f"{BASE_URL}/apps", headers=_headers())
                r.raise_for_status()
                apps = r.json()
                return {
                    "apps": [
                        {"id": a.get("id"), "name": a.get("name"), "players": a.get("players")}
                        for a in (apps if isinstance(apps, list) else [])
                    ]
                }

            elif tool_name == "onesignal_create_segment":
                r = await client.post(
                    f"{BASE_URL}/apps/{app_id}/segments",
                    headers=_headers(),
                    json={"name": arguments["name"], "filters": arguments["filters"]},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("onesignal_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
