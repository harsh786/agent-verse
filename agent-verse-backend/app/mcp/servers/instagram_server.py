"""Instagram MCP server — Graph API for business account management.

Environment:
  INSTAGRAM_ACCESS_TOKEN:        Meta/Facebook Graph API access token
  INSTAGRAM_BUSINESS_ACCOUNT_ID: Instagram Business Account ID
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

GRAPH_BASE = "https://graph.facebook.com/v19.0"

TOOL_DEFINITIONS = [
    {
        "name": "instagram_get_account",
        "description": "Get Instagram Business Account details",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Account ID (uses INSTAGRAM_BUSINESS_ACCOUNT_ID if not provided)",
                },
                "fields": {
                    "type": "string",
                    "default": "id,username,name,biography,followers_count,media_count,profile_picture_url",
                },
            },
        },
    },
    {
        "name": "instagram_list_media",
        "description": "List Instagram media (posts, reels, stories) for a business account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
                "fields": {
                    "type": "string",
                    "default": "id,caption,media_type,media_url,timestamp,like_count,comments_count",
                },
            },
        },
    },
    {
        "name": "instagram_get_media",
        "description": "Get details about a specific Instagram media post",
        "parameters": {
            "type": "object",
            "properties": {
                "media_id": {"type": "string"},
                "fields": {
                    "type": "string",
                    "default": "id,caption,media_type,media_url,timestamp,like_count,comments_count,insights",
                },
            },
            "required": ["media_id"],
        },
    },
    {
        "name": "instagram_create_media_container",
        "description": "Create a media container for an Instagram post (step 1 of 2-step publish)",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "image_url": {"type": "string", "description": "Public URL of the image"},
                "video_url": {"type": "string", "description": "Public URL of the video (for reels)"},
                "caption": {"type": "string"},
                "media_type": {
                    "type": "string",
                    "enum": ["IMAGE", "VIDEO", "REELS"],
                    "default": "IMAGE",
                },
                "is_carousel_item": {"type": "boolean", "default": False},
            },
            "required": ["caption"],
        },
    },
    {
        "name": "instagram_publish_media",
        "description": "Publish an Instagram media container (step 2 of 2-step publish)",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "creation_id": {"type": "string", "description": "Container ID from create_media_container"},
            },
            "required": ["creation_id"],
        },
    },
    {
        "name": "instagram_get_insights",
        "description": "Get insights (analytics) for a business account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "metrics": {
                    "type": "string",
                    "default": "follower_count,impressions,reach,profile_views",
                    "description": "Comma-separated metrics",
                },
                "period": {
                    "type": "string",
                    "enum": ["day", "week", "month", "lifetime"],
                    "default": "week",
                },
            },
        },
    },
]


def _account_id(arguments: dict) -> str:
    return arguments.get("account_id") or os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")


def _params(extra: dict | None = None) -> dict[str, Any]:
    p: dict[str, Any] = {"access_token": os.getenv("INSTAGRAM_ACCESS_TOKEN", "")}
    if extra:
        p.update(extra)
    return p


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
    if not token:
        return {"error": "INSTAGRAM_ACCESS_TOKEN not configured"}
    account_id = _account_id(arguments)
    if not account_id and tool_name != "instagram_get_media":
        return {"error": "account_id required (or set INSTAGRAM_BUSINESS_ACCOUNT_ID)"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "instagram_get_account":
                r = await c.get(
                    f"{GRAPH_BASE}/{account_id}",
                    params=_params({"fields": arguments.get("fields", "id,username,name,followers_count")}),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "instagram_list_media":
                r = await c.get(
                    f"{GRAPH_BASE}/{account_id}/media",
                    params=_params({
                        "fields": arguments.get("fields", "id,caption,media_type,timestamp,like_count"),
                        "limit": arguments.get("limit", 20),
                    }),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "instagram_get_media":
                r = await c.get(
                    f"{GRAPH_BASE}/{arguments['media_id']}",
                    params=_params({"fields": arguments.get("fields", "id,caption,media_type,timestamp")}),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "instagram_create_media_container":
                payload = _params({
                    "caption": arguments.get("caption", ""),
                    "media_type": arguments.get("media_type", "IMAGE"),
                })
                if img := arguments.get("image_url"):
                    payload["image_url"] = img
                if vid := arguments.get("video_url"):
                    payload["video_url"] = vid
                if arguments.get("is_carousel_item"):
                    payload["is_carousel_item"] = "true"
                r = await c.post(f"{GRAPH_BASE}/{account_id}/media", params=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "instagram_publish_media":
                payload = _params({"creation_id": arguments["creation_id"]})
                r = await c.post(f"{GRAPH_BASE}/{account_id}/media_publish", params=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "instagram_get_insights":
                r = await c.get(
                    f"{GRAPH_BASE}/{account_id}/insights",
                    params=_params({
                        "metric": arguments.get("metrics", "follower_count,impressions,reach"),
                        "period": arguments.get("period", "week"),
                    }),
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("instagram_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
