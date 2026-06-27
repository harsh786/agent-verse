"""TikTok MCP server — TikTok for Business API integration.

Environment:
  TIKTOK_ACCESS_TOKEN: TikTok Business API access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TIKTOK_BASE = "https://business-api.tiktok.com/open_api/v1.3"

TOOL_DEFINITIONS = [
    {
        "name": "tiktok_get_user_info",
        "description": "Get TikTok Business account user info",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "tiktok_list_videos",
        "description": "List videos for an advertiser account or creator",
        "parameters": {
            "type": "object",
            "properties": {
                "advertiser_id": {"type": "string"},
                "page": {"type": "integer", "default": 1},
                "page_size": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "tiktok_get_video_insights",
        "description": "Get performance insights for TikTok videos",
        "parameters": {
            "type": "object",
            "properties": {
                "advertiser_id": {"type": "string"},
                "video_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of video IDs",
                },
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["video_play_actions", "video_watched_2s", "reach", "impressions"],
                },
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["advertiser_id"],
        },
    },
    {
        "name": "tiktok_search_videos",
        "description": "Search TikTok videos by keyword using Research API",
        "parameters": {
            "type": "object",
            "properties": {
                "keywords": {"type": "string", "description": "Search keywords"},
                "period_code": {
                    "type": "integer",
                    "description": "7 (7 days), 30 (30 days), 90 (90 days), 120 (120 days)",
                    "default": 7,
                },
                "region_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Country codes, e.g. ['US', 'GB']",
                },
                "max_count": {"type": "integer", "default": 20},
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "tiktok_list_campaigns",
        "description": "List TikTok Ads campaigns for an advertiser",
        "parameters": {
            "type": "object",
            "properties": {
                "advertiser_id": {"type": "string"},
                "primary_status": {
                    "type": "string",
                    "enum": ["STATUS_ALL", "STATUS_ACTIVE", "STATUS_DISABLE", "STATUS_ARCHIVED"],
                    "default": "STATUS_ALL",
                },
                "page": {"type": "integer", "default": 1},
                "page_size": {"type": "integer", "default": 20},
            },
            "required": ["advertiser_id"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("TIKTOK_ACCESS_TOKEN", "")
    return {
        "Access-Token": token,
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("TIKTOK_ACCESS_TOKEN", "")
    if not token:
        return {"error": "TIKTOK_ACCESS_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(base_url=TIKTOK_BASE, headers=_headers(), timeout=30.0) as c:
            if tool_name == "tiktok_get_user_info":
                r = await c.get("/user/info/")
                r.raise_for_status()
                return r.json()

            elif tool_name == "tiktok_list_videos":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "page_size": arguments.get("page_size", 20),
                }
                if adv_id := arguments.get("advertiser_id"):
                    params["advertiser_id"] = adv_id
                r = await c.get("/file/video/ad/search/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "tiktok_get_video_insights":
                payload: dict[str, Any] = {
                    "advertiser_id": arguments["advertiser_id"],
                    "metrics": arguments.get("metrics", ["video_play_actions", "impressions"]),
                    "report_type": "BASIC",
                    "dimensions": ["video_id"],
                }
                if vids := arguments.get("video_ids"):
                    payload["filtering"] = [{"field_name": "video_id", "filter_type": "IN", "filter_value": vids}]
                if start := arguments.get("start_date"):
                    payload["start_date"] = start
                if end := arguments.get("end_date"):
                    payload["end_date"] = end
                r = await c.post("/report/integrated/get/", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "tiktok_search_videos":
                payload = {
                    "query": {
                        "and": [
                            {"operation": "IN", "field_name": "keyword", "field_values": [arguments["keywords"]]}
                        ]
                    },
                    "max_count": arguments.get("max_count", 20),
                    "search_id": "",
                }
                if regions := arguments.get("region_codes"):
                    payload["query"]["and"].append(
                        {"operation": "IN", "field_name": "region_code", "field_values": regions}
                    )
                r = await c.post("/research/video/query/", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "tiktok_list_campaigns":
                payload = {
                    "advertiser_id": arguments["advertiser_id"],
                    "primary_status": arguments.get("primary_status", "STATUS_ALL"),
                    "page": arguments.get("page", 1),
                    "page_size": arguments.get("page_size", 20),
                }
                r = await c.get("/campaign/get/", params=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("tiktok_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
