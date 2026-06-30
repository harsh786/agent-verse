"""Vimeo MCP server — Vimeo video hosting, uploads, folders, and analytics.

Environment:
  VIMEO_ACCESS_TOKEN: Vimeo OAuth2 personal access token from developer settings
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.vimeo.com"

TOOL_DEFINITIONS = [
    {
        "name": "vimeo_list_videos",
        "description": "List videos in the authenticated Vimeo account",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query to filter videos by title"},
                "sort": {"type": "string", "description": "Sort order: date, alphabetical, plays, likes, comments, duration"},
                "direction": {"type": "string", "description": "Sort direction: asc or desc", "default": "desc"},
                "per_page": {"type": "integer", "description": "Number of videos per page (max 100)", "default": 25},
                "page": {"type": "integer", "description": "Page number", "default": 1},
            },
        },
    },
    {
        "name": "vimeo_upload_video",
        "description": "Initiate a Vimeo video upload and get the upload link",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Video title"},
                "description": {"type": "string", "description": "Video description"},
                "file_size": {"type": "integer", "description": "Size of the video file in bytes"},
                "privacy_view": {"type": "string", "description": "Privacy setting: anybody, nobody, password, disable, unlisted", "default": "anybody"},
            },
            "required": ["name", "file_size"],
        },
    },
    {
        "name": "vimeo_get_video",
        "description": "Get details of a specific Vimeo video by video ID",
        "parameters": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string", "description": "Vimeo video ID (numeric)"},
            },
            "required": ["video_id"],
        },
    },
    {
        "name": "vimeo_update_video",
        "description": "Update metadata for a Vimeo video",
        "parameters": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string", "description": "Vimeo video ID"},
                "name": {"type": "string", "description": "Updated video title"},
                "description": {"type": "string", "description": "Updated video description"},
                "privacy_view": {"type": "string", "description": "Updated privacy: anybody, nobody, password, unlisted"},
                "password": {"type": "string", "description": "Password for password-protected videos"},
            },
            "required": ["video_id"],
        },
    },
    {
        "name": "vimeo_list_folders",
        "description": "List folders (projects) in the Vimeo account",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "description": "Number of folders per page (max 100)", "default": 25},
                "page": {"type": "integer", "description": "Page number", "default": 1},
            },
        },
    },
    {
        "name": "vimeo_get_video_stats",
        "description": "Get play statistics for a specific Vimeo video",
        "parameters": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string", "description": "Vimeo video ID"},
            },
            "required": ["video_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("VIMEO_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "VIMEO_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.vimeo.*+json;version=3.4",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "vimeo_list_videos":
                params: dict[str, Any] = {
                    "per_page": arguments.get("per_page", 25),
                    "page": arguments.get("page", 1),
                    "direction": arguments.get("direction", "desc"),
                }
                if "query" in arguments:
                    params["query"] = arguments["query"]
                if "sort" in arguments:
                    params["sort"] = arguments["sort"]
                r = await client.get(f"{BASE}/me/videos", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "videos": [
                        {
                            "uri": v.get("uri"),
                            "name": v.get("name"),
                            "duration": v.get("duration"),
                            "stats": v.get("stats"),
                            "created_time": v.get("created_time"),
                            "privacy": v.get("privacy", {}).get("view"),
                        }
                        for v in data.get("data", [])
                    ],
                    "total": data.get("total", 0),
                    "paging": data.get("paging", {}),
                }

            elif tool_name == "vimeo_upload_video":
                payload: dict[str, Any] = {
                    "name": arguments["name"],
                    "upload": {
                        "approach": "tus",
                        "size": arguments["file_size"],
                    },
                    "privacy": {"view": arguments.get("privacy_view", "anybody")},
                }
                if "description" in arguments:
                    payload["description"] = arguments["description"]
                r = await client.post(f"{BASE}/me/videos", headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "uri": data.get("uri"),
                    "upload_link": data.get("upload", {}).get("upload_link"),
                    "complete_uri": data.get("upload", {}).get("complete_uri"),
                }

            elif tool_name == "vimeo_get_video":
                r = await client.get(f"{BASE}/videos/{arguments['video_id']}", headers=headers)
                r.raise_for_status()
                data = r.json()
                return {
                    "uri": data.get("uri"),
                    "name": data.get("name"),
                    "description": data.get("description"),
                    "duration": data.get("duration"),
                    "stats": data.get("stats"),
                    "privacy": data.get("privacy"),
                    "link": data.get("link"),
                }

            elif tool_name == "vimeo_update_video":
                payload = {}
                if "name" in arguments:
                    payload["name"] = arguments["name"]
                if "description" in arguments:
                    payload["description"] = arguments["description"]
                if "privacy_view" in arguments:
                    payload["privacy"] = {"view": arguments["privacy_view"]}
                    if "password" in arguments:
                        payload["password"] = arguments["password"]
                r = await client.patch(
                    f"{BASE}/videos/{arguments['video_id']}",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return {"updated": True, "video_id": arguments["video_id"]}

            elif tool_name == "vimeo_list_folders":
                params = {
                    "per_page": arguments.get("per_page", 25),
                    "page": arguments.get("page", 1),
                }
                r = await client.get(f"{BASE}/me/projects", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "folders": [
                        {
                            "uri": f.get("uri"),
                            "name": f.get("name"),
                            "created_time": f.get("created_time"),
                        }
                        for f in data.get("data", [])
                    ],
                    "total": data.get("total", 0),
                }

            elif tool_name == "vimeo_get_video_stats":
                r = await client.get(
                    f"{BASE}/videos/{arguments['video_id']}",
                    headers=headers,
                    params={"fields": "stats,name,uri,duration"},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "uri": data.get("uri"),
                    "name": data.get("name"),
                    "stats": data.get("stats", {}),
                    "duration": data.get("duration"),
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("vimeo_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
