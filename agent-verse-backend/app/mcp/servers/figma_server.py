"""Figma MCP server — design file access and collaboration via Figma API.

Environment:
  FIGMA_ACCESS_TOKEN: Figma personal access token or OAuth2 bearer token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

FIGMA_BASE = "https://api.figma.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "figma_list_files",
        "description": "List files in a Figma project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Figma project ID"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "figma_get_file",
        "description": "Get the full document tree for a Figma file",
        "parameters": {
            "type": "object",
            "properties": {
                "file_key": {"type": "string", "description": "Figma file key from the URL"},
                "depth": {
                    "type": "integer",
                    "default": 2,
                    "description": "Depth of the node tree to return",
                },
            },
            "required": ["file_key"],
        },
    },
    {
        "name": "figma_get_components",
        "description": "Get all components defined in a Figma file",
        "parameters": {
            "type": "object",
            "properties": {
                "file_key": {"type": "string"},
            },
            "required": ["file_key"],
        },
    },
    {
        "name": "figma_get_styles",
        "description": "Get all styles (colors, text, effects) defined in a Figma file",
        "parameters": {
            "type": "object",
            "properties": {
                "file_key": {"type": "string"},
            },
            "required": ["file_key"],
        },
    },
    {
        "name": "figma_list_projects",
        "description": "List projects in a Figma team",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Figma team ID"},
            },
            "required": ["team_id"],
        },
    },
    {
        "name": "figma_get_file_comments",
        "description": "Get comments on a Figma file",
        "parameters": {
            "type": "object",
            "properties": {
                "file_key": {"type": "string"},
            },
            "required": ["file_key"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"X-Figma-Token": token}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("FIGMA_ACCESS_TOKEN", "")
    if not token:
        return {"error": "FIGMA_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "figma_list_files":
                project_id = arguments["project_id"]
                r = await c.get(
                    f"{FIGMA_BASE}/projects/{project_id}/files",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "files": [
                        {
                            "key": f.get("key"),
                            "name": f.get("name"),
                            "last_modified": f.get("last_modified"),
                            "thumbnail_url": f.get("thumbnail_url"),
                        }
                        for f in data.get("files", [])
                    ]
                }

            elif tool_name == "figma_get_file":
                file_key = arguments["file_key"]
                r = await c.get(
                    f"{FIGMA_BASE}/files/{file_key}",
                    headers=hdrs,
                    params={"depth": arguments.get("depth", 2)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "name": data.get("name"),
                    "last_modified": data.get("lastModified"),
                    "version": data.get("version"),
                    "document": data.get("document"),
                }

            elif tool_name == "figma_get_components":
                file_key = arguments["file_key"]
                r = await c.get(
                    f"{FIGMA_BASE}/files/{file_key}/components",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "components": [
                        {
                            "key": comp.get("key"),
                            "name": comp.get("name"),
                            "description": comp.get("description"),
                        }
                        for comp in data.get("meta", {}).get("components", [])
                    ]
                }

            elif tool_name == "figma_get_styles":
                file_key = arguments["file_key"]
                r = await c.get(
                    f"{FIGMA_BASE}/files/{file_key}/styles",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "styles": [
                        {
                            "key": s.get("key"),
                            "name": s.get("name"),
                            "style_type": s.get("style_type"),
                            "description": s.get("description"),
                        }
                        for s in data.get("meta", {}).get("styles", [])
                    ]
                }

            elif tool_name == "figma_list_projects":
                team_id = arguments["team_id"]
                r = await c.get(
                    f"{FIGMA_BASE}/teams/{team_id}/projects",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "projects": [
                        {"id": p.get("id"), "name": p.get("name")}
                        for p in data.get("projects", [])
                    ]
                }

            elif tool_name == "figma_get_file_comments":
                file_key = arguments["file_key"]
                r = await c.get(
                    f"{FIGMA_BASE}/files/{file_key}/comments",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "comments": [
                        {
                            "id": cm.get("id"),
                            "message": cm.get("message"),
                            "user": cm.get("user", {}).get("name"),
                            "created_at": cm.get("created_at"),
                        }
                        for cm in data.get("comments", [])
                    ]
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("figma_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
