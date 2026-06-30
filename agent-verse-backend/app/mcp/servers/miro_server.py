"""Miro MCP server — visual collaboration boards, sticky notes, frames, and items.

Environment:
  MIRO_ACCESS_TOKEN: Miro OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.miro.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "miro_list_boards",
        "description": "List Miro boards accessible to the user",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Filter by team ID"},
                "query": {"type": "string", "description": "Search query for board names"},
                "limit": {"type": "integer", "default": 10},
                "cursor": {"type": "string", "description": "Pagination cursor"},
            },
        },
    },
    {
        "name": "miro_create_board",
        "description": "Create a new Miro board",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "team_id": {"type": "string"},
                "policy": {
                    "type": "object",
                    "description": "Board sharing policy",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "miro_get_board",
        "description": "Get details of a specific Miro board",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
            },
            "required": ["board_id"],
        },
    },
    {
        "name": "miro_create_sticky_note",
        "description": "Create a sticky note on a Miro board",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "content": {"type": "string", "description": "Text content of the sticky note"},
                "color": {
                    "type": "string",
                    "enum": ["gray", "light_yellow", "yellow", "orange", "light_green", "green", "dark_green", "cyan", "light_pink", "pink", "violet", "red", "light_blue", "blue", "dark_blue", "black"],
                    "default": "yellow",
                },
                "x": {"type": "number", "default": 0.0},
                "y": {"type": "number", "default": 0.0},
                "width": {"type": "number", "default": 200.0},
            },
            "required": ["board_id", "content"],
        },
    },
    {
        "name": "miro_list_items",
        "description": "List all items on a Miro board",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "type": {
                    "type": "string",
                    "description": "Filter by item type: sticky_note, shape, text, frame, image, etc.",
                },
                "limit": {"type": "integer", "default": 50},
                "cursor": {"type": "string"},
            },
            "required": ["board_id"],
        },
    },
    {
        "name": "miro_create_frame",
        "description": "Create a frame on a Miro board to group items",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "title": {"type": "string"},
                "x": {"type": "number", "default": 0.0},
                "y": {"type": "number", "default": 0.0},
                "width": {"type": "number", "default": 600.0},
                "height": {"type": "number", "default": 400.0},
            },
            "required": ["board_id", "title"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("MIRO_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "MIRO_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "miro_list_boards":
                params: dict[str, Any] = {"limit": arguments.get("limit", 10)}
                if tid := arguments.get("team_id"):
                    params["team_id"] = tid
                if q := arguments.get("query"):
                    params["query"] = q
                if cursor := arguments.get("cursor"):
                    params["cursor"] = cursor
                r = await c.get(f"{BASE}/boards", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "miro_create_board":
                payload: dict[str, Any] = {"name": arguments["name"]}
                for field in ("description", "team_id", "policy"):
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.post(f"{BASE}/boards", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "miro_get_board":
                bid = arguments["board_id"]
                r = await c.get(f"{BASE}/boards/{bid}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "miro_create_sticky_note":
                bid = arguments["board_id"]
                payload = {
                    "data": {
                        "content": arguments["content"],
                        "shape": "square",
                    },
                    "style": {"fillColor": arguments.get("color", "yellow")},
                    "geometry": {
                        "width": arguments.get("width", 200.0),
                    },
                    "position": {
                        "x": arguments.get("x", 0.0),
                        "y": arguments.get("y", 0.0),
                    },
                }
                r = await c.post(f"{BASE}/boards/{bid}/sticky_notes", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "miro_list_items":
                bid = arguments["board_id"]
                params = {"limit": arguments.get("limit", 50)}
                if itype := arguments.get("type"):
                    params["type"] = itype
                if cursor := arguments.get("cursor"):
                    params["cursor"] = cursor
                r = await c.get(f"{BASE}/boards/{bid}/items", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "miro_create_frame":
                bid = arguments["board_id"]
                payload = {
                    "data": {"title": arguments["title"], "format": "custom", "type": "freeform"},
                    "geometry": {
                        "width": arguments.get("width", 600.0),
                        "height": arguments.get("height", 400.0),
                    },
                    "position": {
                        "x": arguments.get("x", 0.0),
                        "y": arguments.get("y", 0.0),
                    },
                }
                r = await c.post(f"{BASE}/boards/{bid}/frames", json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("miro_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
