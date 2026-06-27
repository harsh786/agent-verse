"""YouTube MCP server — YouTube Data API v3 for video and channel data.

Environment:
  YOUTUBE_API_KEY:      YouTube Data API v3 key (for public data reads)
  YOUTUBE_ACCESS_TOKEN: OAuth 2.0 token (for authenticated operations)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

YOUTUBE_BASE = "https://www.googleapis.com/youtube/v3"

TOOL_DEFINITIONS = [
    {
        "name": "youtube_search",
        "description": "Search YouTube for videos, channels, or playlists",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "default": 10, "description": "1–50"},
                "type": {
                    "type": "string",
                    "enum": ["video", "channel", "playlist"],
                    "default": "video",
                },
                "order": {
                    "type": "string",
                    "enum": ["relevance", "date", "rating", "viewCount", "title"],
                    "default": "relevance",
                },
                "published_after": {"type": "string", "description": "RFC 3339 datetime"},
                "language": {"type": "string", "description": "ISO 639-1 language code"},
                "region_code": {"type": "string", "description": "ISO 3166-1 alpha-2 country code"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "youtube_get_video",
        "description": "Get detailed information about a YouTube video by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
                "parts": {
                    "type": "string",
                    "default": "snippet,statistics,contentDetails",
                    "description": "Comma-separated parts to retrieve",
                },
            },
            "required": ["video_id"],
        },
    },
    {
        "name": "youtube_list_channel_videos",
        "description": "List videos from a specific YouTube channel",
        "parameters": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "max_results": {"type": "integer", "default": 20},
                "order": {
                    "type": "string",
                    "enum": ["date", "rating", "relevance", "title", "viewCount"],
                    "default": "date",
                },
                "published_after": {"type": "string"},
                "page_token": {"type": "string", "description": "Pagination token"},
            },
            "required": ["channel_id"],
        },
    },
    {
        "name": "youtube_get_channel",
        "description": "Get information about a YouTube channel",
        "parameters": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Channel ID (UCxxx...)"},
                "username": {"type": "string", "description": "Channel username/handle (alternative to ID)"},
                "parts": {
                    "type": "string",
                    "default": "snippet,statistics,brandingSettings",
                },
            },
        },
    },
    {
        "name": "youtube_list_playlists",
        "description": "List playlists for a YouTube channel",
        "parameters": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "max_results": {"type": "integer", "default": 20},
                "page_token": {"type": "string"},
            },
            "required": ["channel_id"],
        },
    },
    {
        "name": "youtube_list_playlist_items",
        "description": "List videos in a YouTube playlist",
        "parameters": {
            "type": "object",
            "properties": {
                "playlist_id": {"type": "string"},
                "max_results": {"type": "integer", "default": 50},
                "page_token": {"type": "string"},
            },
            "required": ["playlist_id"],
        },
    },
    {
        "name": "youtube_get_video_comments",
        "description": "Get top-level comments for a YouTube video",
        "parameters": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
                "max_results": {"type": "integer", "default": 20},
                "order": {"type": "string", "enum": ["time", "relevance"], "default": "relevance"},
                "search_terms": {"type": "string"},
            },
            "required": ["video_id"],
        },
    },
]


def _api_key() -> str:
    return os.getenv("YOUTUBE_API_KEY", "")


def _headers() -> dict[str, str]:
    token = os.getenv("YOUTUBE_ACCESS_TOKEN", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = _api_key()
    token = os.getenv("YOUTUBE_ACCESS_TOKEN", "")
    if not api_key and not token:
        return {"error": "YOUTUBE_API_KEY or YOUTUBE_ACCESS_TOKEN must be configured"}

    def _params(extra: dict | None = None) -> dict[str, Any]:
        p: dict[str, Any] = {}
        if api_key:
            p["key"] = api_key
        if extra:
            p.update(extra)
        return p

    try:
        async with httpx.AsyncClient(headers=_headers(), timeout=30.0) as c:
            if tool_name == "youtube_search":
                params = _params({
                    "part": "snippet",
                    "q": arguments["query"],
                    "maxResults": arguments.get("max_results", 10),
                    "type": arguments.get("type", "video"),
                    "order": arguments.get("order", "relevance"),
                })
                for key, api_key_name in [
                    ("published_after", "publishedAfter"),
                    ("language", "relevanceLanguage"),
                    ("region_code", "regionCode"),
                ]:
                    if v := arguments.get(key):
                        params[api_key_name] = v
                r = await c.get(f"{YOUTUBE_BASE}/search", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "youtube_get_video":
                r = await c.get(
                    f"{YOUTUBE_BASE}/videos",
                    params=_params({
                        "part": arguments.get("parts", "snippet,statistics,contentDetails"),
                        "id": arguments["video_id"],
                    }),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "youtube_list_channel_videos":
                params = _params({
                    "part": "snippet",
                    "channelId": arguments["channel_id"],
                    "maxResults": arguments.get("max_results", 20),
                    "order": arguments.get("order", "date"),
                    "type": "video",
                })
                if pa := arguments.get("published_after"):
                    params["publishedAfter"] = pa
                if pt := arguments.get("page_token"):
                    params["pageToken"] = pt
                r = await c.get(f"{YOUTUBE_BASE}/search", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "youtube_get_channel":
                p: dict[str, Any] = _params({
                    "part": arguments.get("parts", "snippet,statistics"),
                })
                if cid := arguments.get("channel_id"):
                    p["id"] = cid
                elif uname := arguments.get("username"):
                    p["forHandle"] = uname
                else:
                    return {"error": "Either channel_id or username required"}
                r = await c.get(f"{YOUTUBE_BASE}/channels", params=p)
                r.raise_for_status()
                return r.json()

            elif tool_name == "youtube_list_playlists":
                params = _params({
                    "part": "snippet,contentDetails",
                    "channelId": arguments["channel_id"],
                    "maxResults": arguments.get("max_results", 20),
                })
                if pt := arguments.get("page_token"):
                    params["pageToken"] = pt
                r = await c.get(f"{YOUTUBE_BASE}/playlists", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "youtube_list_playlist_items":
                params = _params({
                    "part": "snippet,contentDetails",
                    "playlistId": arguments["playlist_id"],
                    "maxResults": arguments.get("max_results", 50),
                })
                if pt := arguments.get("page_token"):
                    params["pageToken"] = pt
                r = await c.get(f"{YOUTUBE_BASE}/playlistItems", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "youtube_get_video_comments":
                params = _params({
                    "part": "snippet",
                    "videoId": arguments["video_id"],
                    "maxResults": arguments.get("max_results", 20),
                    "order": arguments.get("order", "relevance"),
                })
                if search := arguments.get("search_terms"):
                    params["searchTerms"] = search
                r = await c.get(f"{YOUTUBE_BASE}/commentThreads", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("youtube_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
