"""Hootsuite MCP server — Hootsuite social media profile management, scheduling, and analytics.

Environment:
  HOOTSUITE_ACCESS_TOKEN: Hootsuite OAuth2 access token from developer portal
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://platform.hootsuite.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "hootsuite_list_profiles",
        "description": "List all connected social profiles in the Hootsuite account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "hootsuite_schedule_post",
        "description": "Schedule a social media post through Hootsuite",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Post text content"},
                "social_profile_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of Hootsuite social profile IDs to post to",
                },
                "scheduled_send_time": {"type": "string", "description": "ISO 8601 datetime to send the post"},
                "media_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "URLs of media (images/videos) to attach",
                },
            },
            "required": ["text", "social_profile_ids"],
        },
    },
    {
        "name": "hootsuite_list_scheduled_posts",
        "description": "List scheduled (pending) posts in Hootsuite",
        "parameters": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "Post state: SCHEDULED, SENT, FAILED", "default": "SCHEDULED"},
                "start_time": {"type": "string", "description": "ISO 8601 start of time range"},
                "end_time": {"type": "string", "description": "ISO 8601 end of time range"},
                "limit": {"type": "integer", "description": "Number of posts to return (max 50)", "default": 20},
            },
        },
    },
    {
        "name": "hootsuite_get_analytics",
        "description": "Get social media analytics for a Hootsuite social profile",
        "parameters": {
            "type": "object",
            "properties": {
                "social_profile_id": {"type": "string", "description": "Hootsuite social profile ID"},
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Metric names e.g. TOTAL_REACH, TOTAL_IMPRESSIONS, TOTAL_ENGAGEMENTS",
                },
                "start_time": {"type": "string", "description": "ISO 8601 start of analytics period"},
                "end_time": {"type": "string", "description": "ISO 8601 end of analytics period"},
            },
            "required": ["social_profile_id", "metrics", "start_time", "end_time"],
        },
    },
    {
        "name": "hootsuite_list_teams",
        "description": "List all teams within the Hootsuite organization",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "hootsuite_bulk_schedule",
        "description": "Schedule multiple social media posts in bulk via Hootsuite",
        "parameters": {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "social_profile_ids": {"type": "array", "items": {"type": "string"}},
                            "scheduled_send_time": {"type": "string"},
                        },
                    },
                    "description": "Array of post objects to schedule in bulk",
                },
            },
            "required": ["messages"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("HOOTSUITE_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "HOOTSUITE_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "hootsuite_list_profiles":
                r = await client.get(f"{BASE}/socialProfiles", headers=headers)
                r.raise_for_status()
                data = r.json()
                return {
                    "profiles": [
                        {
                            "id": p.get("id"),
                            "type": p.get("type"),
                            "username": p.get("username"),
                            "avatar_url": p.get("avatarUrl"),
                        }
                        for p in data.get("data", [])
                    ],
                }

            elif tool_name == "hootsuite_schedule_post":
                payload: dict[str, Any] = {
                    "text": arguments["text"],
                    "socialProfileIds": arguments["social_profile_ids"],
                }
                if "scheduled_send_time" in arguments:
                    payload["scheduledSendTime"] = arguments["scheduled_send_time"]
                if "media_urls" in arguments:
                    payload["mediaUrls"] = arguments["media_urls"]
                r = await client.post(f"{BASE}/messages", headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
                msg = data.get("data", {})
                return {
                    "id": msg.get("id"),
                    "state": msg.get("state"),
                    "scheduled_send_time": msg.get("scheduledSendTime"),
                }

            elif tool_name == "hootsuite_list_scheduled_posts":
                params: dict[str, Any] = {
                    "state": arguments.get("state", "SCHEDULED"),
                    "limit": arguments.get("limit", 20),
                }
                if "start_time" in arguments:
                    params["startTime"] = arguments["start_time"]
                if "end_time" in arguments:
                    params["endTime"] = arguments["end_time"]
                r = await client.get(f"{BASE}/messages", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "messages": [
                        {
                            "id": m.get("id"),
                            "text": m.get("text"),
                            "state": m.get("state"),
                            "scheduled_send_time": m.get("scheduledSendTime"),
                        }
                        for m in data.get("data", [])
                    ],
                }

            elif tool_name == "hootsuite_get_analytics":
                payload = {
                    "socialProfileId": arguments["social_profile_id"],
                    "metrics": arguments["metrics"],
                    "startTime": arguments["start_time"],
                    "endTime": arguments["end_time"],
                }
                r = await client.post(f"{BASE}/analytics/posts", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hootsuite_list_teams":
                r = await client.get(f"{BASE}/teams", headers=headers)
                r.raise_for_status()
                data = r.json()
                return {"teams": data.get("data", [])}

            elif tool_name == "hootsuite_bulk_schedule":
                results = []
                for message in arguments["messages"]:
                    payload = {
                        "text": message.get("text", ""),
                        "socialProfileIds": message.get("social_profile_ids", []),
                    }
                    if "scheduled_send_time" in message:
                        payload["scheduledSendTime"] = message["scheduled_send_time"]
                    r = await client.post(f"{BASE}/messages", headers=headers, json=payload)
                    if r.status_code in (200, 201):
                        results.append({"status": "success", "id": r.json().get("data", {}).get("id")})
                    else:
                        results.append({"status": "error", "code": r.status_code})
                return {"scheduled": len([x for x in results if x["status"] == "success"]), "results": results}

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("hootsuite_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
