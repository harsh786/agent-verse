"""Sprout Social MCP server — Sprout Social social media management, analytics, and tasks.

Environment:
  SPROUT_SOCIAL_ACCESS_TOKEN: Sprout Social OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.sproutsocial.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "sprout_social_list_profiles",
        "description": "List all connected social media profiles in Sprout Social",
        "parameters": {
            "type": "object",
            "properties": {
                "network": {"type": "string", "description": "Filter by network: twitter, facebook, instagram, linkedin"},
            },
        },
    },
    {
        "name": "sprout_social_schedule_message",
        "description": "Schedule a social media message through Sprout Social",
        "parameters": {
            "type": "object",
            "properties": {
                "profile_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Sprout Social profile IDs to post to",
                },
                "text": {"type": "string", "description": "Message text content"},
                "scheduled_at": {"type": "string", "description": "ISO 8601 scheduled send datetime"},
                "media_attachments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Media file URLs to attach",
                },
            },
            "required": ["profile_ids", "text"],
        },
    },
    {
        "name": "sprout_social_list_messages",
        "description": "List sent or scheduled messages in Sprout Social inbox",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: sent, scheduled, draft, failed"},
                "page": {"type": "integer", "description": "Page number", "default": 1},
                "per_page": {"type": "integer", "description": "Results per page", "default": 20},
            },
        },
    },
    {
        "name": "sprout_social_get_analytics",
        "description": "Get analytics report for social profiles in Sprout Social",
        "parameters": {
            "type": "object",
            "properties": {
                "profile_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Profile IDs to retrieve analytics for",
                },
                "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Metric fields: impressions, engagements, reach, followers_gained",
                },
            },
            "required": ["profile_ids", "start_date", "end_date"],
        },
    },
    {
        "name": "sprout_social_list_tags",
        "description": "List all tags used for message organization in Sprout Social",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number", "default": 1},
            },
        },
    },
    {
        "name": "sprout_social_create_task",
        "description": "Create a task/action item in Sprout Social for team collaboration",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Task subject/title"},
                "message": {"type": "string", "description": "Task description or notes"},
                "assignee_id": {"type": "string", "description": "Sprout Social user ID to assign task to"},
                "due_at": {"type": "string", "description": "ISO 8601 due date for the task"},
            },
            "required": ["subject"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("SPROUT_SOCIAL_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "SPROUT_SOCIAL_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "sprout_social_list_profiles":
                params: dict[str, Any] = {}
                if "network" in arguments:
                    params["network"] = arguments["network"]
                r = await client.get(f"{BASE}/profiles", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "profiles": [
                        {
                            "id": p.get("id"),
                            "name": p.get("name"),
                            "network": p.get("network_type"),
                            "username": p.get("username"),
                        }
                        for p in data.get("data", [])
                    ],
                }

            elif tool_name == "sprout_social_schedule_message":
                payload: dict[str, Any] = {
                    "profile_ids": arguments["profile_ids"],
                    "text": arguments["text"],
                }
                if "scheduled_at" in arguments:
                    payload["scheduled_at"] = arguments["scheduled_at"]
                if "media_attachments" in arguments:
                    payload["media"] = [{"url": url} for url in arguments["media_attachments"]]
                r = await client.post(f"{BASE}/message", headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("data", {}).get("id"),
                    "status": data.get("data", {}).get("status"),
                    "scheduled_at": data.get("data", {}).get("scheduled_at"),
                }

            elif tool_name == "sprout_social_list_messages":
                params = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 20),
                }
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await client.get(f"{BASE}/messages", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "messages": [
                        {
                            "id": m.get("id"),
                            "text": m.get("text"),
                            "status": m.get("status"),
                            "scheduled_at": m.get("scheduled_at"),
                        }
                        for m in data.get("data", [])
                    ],
                    "pagination": data.get("paging", {}),
                }

            elif tool_name == "sprout_social_get_analytics":
                payload = {
                    "profile_ids": arguments["profile_ids"],
                    "start_date": arguments["start_date"],
                    "end_date": arguments["end_date"],
                }
                if "fields" in arguments:
                    payload["fields"] = arguments["fields"]
                r = await client.post(f"{BASE}/analytics/profiles", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "sprout_social_list_tags":
                r = await client.get(
                    f"{BASE}/tags",
                    headers=headers,
                    params={"page": arguments.get("page", 1)},
                )
                r.raise_for_status()
                data = r.json()
                return {"tags": data.get("data", [])}

            elif tool_name == "sprout_social_create_task":
                payload = {"subject": arguments["subject"]}
                for field in ("message", "assignee_id", "due_at"):
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await client.post(f"{BASE}/tasks", headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("data", {}).get("id"), "subject": arguments["subject"]}

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("sprout_social_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
