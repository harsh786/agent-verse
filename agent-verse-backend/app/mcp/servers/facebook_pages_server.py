"""Facebook Pages MCP server — Facebook Page posts, insights, comments, and scheduling.

Environment:
  FACEBOOK_ACCESS_TOKEN: Facebook Page access token or user token with pages_manage_posts scope
  FACEBOOK_PAGE_ID: Facebook Page ID to manage
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://graph.facebook.com/v18.0"

TOOL_DEFINITIONS = [
    {
        "name": "facebook_pages_list_pages",
        "description": "List all Facebook Pages the authenticated user manages",
        "parameters": {
            "type": "object",
            "properties": {
                "fields": {"type": "string", "description": "Comma-separated fields: id,name,fan_count,category,picture"},
            },
        },
    },
    {
        "name": "facebook_pages_get_page_insights",
        "description": "Get insights/analytics data for a Facebook Page",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Facebook Page ID (uses FACEBOOK_PAGE_ID if omitted)"},
                "metric": {
                    "type": "string",
                    "description": "Comma-separated metrics: page_impressions, page_reach, page_engaged_users, page_fan_adds",
                },
                "period": {"type": "string", "description": "Time period: day, week, days_28, month", "default": "day"},
            },
            "required": ["metric"],
        },
    },
    {
        "name": "facebook_pages_create_post",
        "description": "Create a new post on a Facebook Page",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Post text content"},
                "link": {"type": "string", "description": "URL to attach as a link preview"},
                "page_id": {"type": "string", "description": "Facebook Page ID (uses FACEBOOK_PAGE_ID if omitted)"},
                "published": {"type": "boolean", "description": "Whether to publish immediately", "default": True},
            },
            "required": ["message"],
        },
    },
    {
        "name": "facebook_pages_list_posts",
        "description": "List posts on a Facebook Page",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Facebook Page ID (uses FACEBOOK_PAGE_ID if omitted)"},
                "fields": {"type": "string", "description": "Fields: id,message,created_time,likes,comments", "default": "id,message,created_time"},
                "limit": {"type": "integer", "description": "Number of posts to return", "default": 25},
                "after": {"type": "string", "description": "Pagination cursor for next page"},
            },
        },
    },
    {
        "name": "facebook_pages_respond_to_comment",
        "description": "Respond to a comment on a Facebook Page post",
        "parameters": {
            "type": "object",
            "properties": {
                "comment_id": {"type": "string", "description": "Facebook comment ID to respond to"},
                "message": {"type": "string", "description": "Reply message text"},
            },
            "required": ["comment_id", "message"],
        },
    },
    {
        "name": "facebook_pages_schedule_post",
        "description": "Schedule a future post on a Facebook Page",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Post text content"},
                "scheduled_publish_time": {"type": "integer", "description": "Unix timestamp for when to publish"},
                "link": {"type": "string", "description": "URL to attach as link preview"},
                "page_id": {"type": "string", "description": "Facebook Page ID (uses FACEBOOK_PAGE_ID if omitted)"},
            },
            "required": ["message", "scheduled_publish_time"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("FACEBOOK_ACCESS_TOKEN", "")
    default_page_id = os.getenv("FACEBOOK_PAGE_ID", "")
    if not access_token:
        return {"error": "FACEBOOK_ACCESS_TOKEN not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "facebook_pages_list_pages":
                fields = arguments.get("fields", "id,name,fan_count,category")
                r = await client.get(
                    f"{BASE}/me/accounts",
                    params={"access_token": access_token, "fields": fields},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "pages": [
                        {
                            "id": p.get("id"),
                            "name": p.get("name"),
                            "fan_count": p.get("fan_count"),
                            "category": p.get("category"),
                        }
                        for p in data.get("data", [])
                    ],
                }

            elif tool_name == "facebook_pages_get_page_insights":
                page_id = arguments.get("page_id") or default_page_id
                if not page_id:
                    return {"error": "FACEBOOK_PAGE_ID not configured and page_id not provided"}
                r = await client.get(
                    f"{BASE}/{page_id}/insights",
                    params={
                        "access_token": access_token,
                        "metric": arguments["metric"],
                        "period": arguments.get("period", "day"),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {"insights": data.get("data", [])}

            elif tool_name == "facebook_pages_create_post":
                page_id = arguments.get("page_id") or default_page_id
                if not page_id:
                    return {"error": "FACEBOOK_PAGE_ID not configured and page_id not provided"}
                params: dict[str, Any] = {
                    "access_token": access_token,
                    "message": arguments["message"],
                    "published": "true" if arguments.get("published", True) else "false",
                }
                if "link" in arguments:
                    params["link"] = arguments["link"]
                r = await client.post(f"{BASE}/{page_id}/feed", params=params)
                r.raise_for_status()
                return {"id": r.json().get("id"), "success": True}

            elif tool_name == "facebook_pages_list_posts":
                page_id = arguments.get("page_id") or default_page_id
                if not page_id:
                    return {"error": "FACEBOOK_PAGE_ID not configured and page_id not provided"}
                params = {
                    "access_token": access_token,
                    "fields": arguments.get("fields", "id,message,created_time"),
                    "limit": arguments.get("limit", 25),
                }
                if "after" in arguments:
                    params["after"] = arguments["after"]
                r = await client.get(f"{BASE}/{page_id}/feed", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "posts": data.get("data", []),
                    "paging": data.get("paging", {}),
                }

            elif tool_name == "facebook_pages_respond_to_comment":
                r = await client.post(
                    f"{BASE}/{arguments['comment_id']}/comments",
                    params={
                        "access_token": access_token,
                        "message": arguments["message"],
                    },
                )
                r.raise_for_status()
                return {"id": r.json().get("id"), "success": True}

            elif tool_name == "facebook_pages_schedule_post":
                page_id = arguments.get("page_id") or default_page_id
                if not page_id:
                    return {"error": "FACEBOOK_PAGE_ID not configured and page_id not provided"}
                params = {
                    "access_token": access_token,
                    "message": arguments["message"],
                    "scheduled_publish_time": arguments["scheduled_publish_time"],
                    "published": "false",
                }
                if "link" in arguments:
                    params["link"] = arguments["link"]
                r = await client.post(f"{BASE}/{page_id}/feed", params=params)
                r.raise_for_status()
                return {"id": r.json().get("id"), "scheduled": True}

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("facebook_pages_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
