"""Loom MCP server — video messaging via Loom API v1.

Environment:
  LOOM_API_KEY: Loom API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

LOOM_BASE = "https://api.loom.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "loom_list_videos",
        "description": "List videos in the Loom workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "folder_id": {"type": "string", "description": "Filter by folder ID"},
                "space_id": {"type": "string", "description": "Filter by space ID"},
            },
        },
    },
    {
        "name": "loom_get_video",
        "description": "Get details and metadata for a specific Loom video",
        "parameters": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
            },
            "required": ["video_id"],
        },
    },
    {
        "name": "loom_list_spaces",
        "description": "List all spaces in the Loom workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "loom_list_folders",
        "description": "List folders within a Loom space",
        "parameters": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string", "description": "Space ID to list folders from"},
            },
            "required": ["space_id"],
        },
    },
    {
        "name": "loom_get_video_transcript",
        "description": "Get the transcript of a Loom video",
        "parameters": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
            },
            "required": ["video_id"],
        },
    },
    {
        "name": "loom_download_video",
        "description": "Get a download URL for a Loom video",
        "parameters": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
            },
            "required": ["video_id"],
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("LOOM_API_KEY", "")
    if not api_key:
        return {"error": "LOOM_API_KEY not configured"}

    hdrs = _headers(api_key)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "loom_list_videos":
                params: dict[str, Any] = {"limit": arguments.get("limit", 20)}
                if arguments.get("folder_id"):
                    params["folder_id"] = arguments["folder_id"]
                if arguments.get("space_id"):
                    params["space_id"] = arguments["space_id"]
                r = await c.get(f"{LOOM_BASE}/videos", headers=hdrs, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "videos": [
                        {
                            "id": v.get("id"),
                            "title": v.get("title"),
                            "duration": v.get("duration"),
                            "created_at": v.get("created_at"),
                            "share_url": v.get("share_url"),
                        }
                        for v in data.get("data", [])
                    ]
                }

            elif tool_name == "loom_get_video":
                video_id = arguments["video_id"]
                r = await c.get(f"{LOOM_BASE}/videos/{video_id}", headers=hdrs)
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "description": data.get("description"),
                    "duration": data.get("duration"),
                    "share_url": data.get("share_url"),
                    "created_at": data.get("created_at"),
                    "view_count": data.get("view_count"),
                }

            elif tool_name == "loom_list_spaces":
                r = await c.get(
                    f"{LOOM_BASE}/spaces",
                    headers=hdrs,
                    params={"limit": arguments.get("limit", 20)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "spaces": [
                        {"id": s.get("id"), "name": s.get("name"), "type": s.get("type")}
                        for s in data.get("data", [])
                    ]
                }

            elif tool_name == "loom_list_folders":
                space_id = arguments["space_id"]
                r = await c.get(
                    f"{LOOM_BASE}/spaces/{space_id}/folders",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "folders": [
                        {"id": f.get("id"), "name": f.get("name")}
                        for f in data.get("data", [])
                    ]
                }

            elif tool_name == "loom_get_video_transcript":
                video_id = arguments["video_id"]
                r = await c.get(f"{LOOM_BASE}/videos/{video_id}/transcript", headers=hdrs)
                r.raise_for_status()
                data = r.json()
                return {
                    "video_id": video_id,
                    "transcript": data.get("transcript", []),
                    "language": data.get("language"),
                }

            elif tool_name == "loom_download_video":
                video_id = arguments["video_id"]
                r = await c.get(f"{LOOM_BASE}/videos/{video_id}/download", headers=hdrs)
                r.raise_for_status()
                data = r.json()
                return {
                    "video_id": video_id,
                    "download_url": data.get("url"),
                    "expires_at": data.get("expires_at"),
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("loom_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
