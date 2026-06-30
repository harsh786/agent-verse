"""Wistia MCP server — Wistia video hosting, media management, and analytics.

Environment:
  WISTIA_API_PASSWORD: Wistia API password (use 'api' as username with basic auth)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.wistia.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "wistia_list_medias",
        "description": "List all media files (videos) in the Wistia account",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Filter by Wistia project hashed ID"},
                "name": {"type": "string", "description": "Filter by media name (partial match)"},
                "sort_by": {"type": "string", "description": "Sort field: name, created, updated, duration, file_size"},
                "sort_direction": {"type": "integer", "description": "Sort direction: 1=ascending, 0=descending", "default": 0},
                "page": {"type": "integer", "description": "Page number", "default": 1},
                "per_page": {"type": "integer", "description": "Results per page (max 100)", "default": 25},
            },
        },
    },
    {
        "name": "wistia_get_media",
        "description": "Get details of a specific Wistia media by hashed ID",
        "parameters": {
            "type": "object",
            "properties": {
                "media_id": {"type": "string", "description": "Wistia media hashed ID"},
            },
            "required": ["media_id"],
        },
    },
    {
        "name": "wistia_list_projects",
        "description": "List projects (channels) in the Wistia account",
        "parameters": {
            "type": "object",
            "properties": {
                "sort_by": {"type": "string", "description": "Sort field: name, mediaCount, created, updated"},
                "sort_direction": {"type": "integer", "description": "1=ascending, 0=descending", "default": 0},
                "page": {"type": "integer", "description": "Page number", "default": 1},
                "per_page": {"type": "integer", "description": "Results per page (max 100)", "default": 25},
            },
        },
    },
    {
        "name": "wistia_get_project_stats",
        "description": "Get view and engagement statistics for a Wistia project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Wistia project hashed ID"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "wistia_get_media_stats",
        "description": "Get detailed view and engagement statistics for a Wistia media",
        "parameters": {
            "type": "object",
            "properties": {
                "media_id": {"type": "string", "description": "Wistia media hashed ID"},
            },
            "required": ["media_id"],
        },
    },
    {
        "name": "wistia_create_project",
        "description": "Create a new project (channel) in Wistia",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "anonymize_visitors": {"type": "boolean", "description": "Anonymize visitor data for GDPR", "default": False},
            },
            "required": ["name"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_password = os.getenv("WISTIA_API_PASSWORD", "")
    if not api_password:
        return {"error": "WISTIA_API_PASSWORD not configured"}

    auth = httpx.BasicAuth("api", api_password)

    async with httpx.AsyncClient(timeout=30.0, auth=auth) as client:
        try:
            if tool_name == "wistia_list_medias":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                    "sort_direction": arguments.get("sort_direction", 0),
                }
                if "project_id" in arguments:
                    params["project_id"] = arguments["project_id"]
                if "name" in arguments:
                    params["name"] = arguments["name"]
                if "sort_by" in arguments:
                    params["sort_by"] = arguments["sort_by"]
                r = await client.get(f"{BASE}/medias.json", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "medias": [
                        {
                            "id": m.get("id"),
                            "hashed_id": m.get("hashed_id"),
                            "name": m.get("name"),
                            "duration": m.get("duration"),
                            "created": m.get("created"),
                            "stats": m.get("stats"),
                        }
                        for m in (data if isinstance(data, list) else [])
                    ],
                }

            elif tool_name == "wistia_get_media":
                r = await client.get(f"{BASE}/medias/{arguments['media_id']}.json")
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "hashed_id": data.get("hashed_id"),
                    "name": data.get("name"),
                    "duration": data.get("duration"),
                    "description": data.get("description"),
                    "stats": data.get("stats"),
                    "embed_code": data.get("embed_code"),
                }

            elif tool_name == "wistia_list_projects":
                params = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                    "sort_direction": arguments.get("sort_direction", 0),
                }
                if "sort_by" in arguments:
                    params["sort_by"] = arguments["sort_by"]
                r = await client.get(f"{BASE}/projects.json", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "projects": [
                        {
                            "id": p.get("id"),
                            "hashed_id": p.get("hashed_id"),
                            "name": p.get("name"),
                            "media_count": p.get("mediaCount"),
                            "created": p.get("created"),
                        }
                        for p in (data if isinstance(data, list) else [])
                    ],
                }

            elif tool_name == "wistia_get_project_stats":
                r = await client.get(f"{BASE}/projects/{arguments['project_id']}.json")
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "hashed_id": data.get("hashed_id"),
                    "name": data.get("name"),
                    "media_count": data.get("mediaCount"),
                    "anonymous_can_see_it": data.get("anonymousCanSeeIt"),
                }

            elif tool_name == "wistia_get_media_stats":
                r = await client.get(f"{BASE}/stats/medias/{arguments['media_id']}.json")
                r.raise_for_status()
                return r.json()

            elif tool_name == "wistia_create_project":
                payload: dict[str, Any] = {"name": arguments["name"]}
                if "anonymize_visitors" in arguments:
                    payload["anonymize_visitors"] = arguments["anonymize_visitors"]
                r = await client.post(f"{BASE}/projects.json", json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "hashed_id": data.get("hashed_id"),
                    "name": data.get("name"),
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("wistia_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
