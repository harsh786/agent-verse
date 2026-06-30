"""Pinterest MCP server — Pinterest boards, pins, analytics, and search.

Environment:
  PINTEREST_ACCESS_TOKEN: Pinterest OAuth2 access token from developer portal
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.pinterest.com/v5"

TOOL_DEFINITIONS = [
    {
        "name": "pinterest_list_boards",
        "description": "List boards for the authenticated Pinterest user",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "description": "Number of boards per page (max 250)", "default": 25},
                "bookmark": {"type": "string", "description": "Pagination cursor for next page"},
                "privacy": {"type": "string", "description": "Filter by privacy: PUBLIC, PROTECTED, SECRET"},
            },
        },
    },
    {
        "name": "pinterest_create_pin",
        "description": "Create a new pin on a Pinterest board",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string", "description": "Pinterest board ID to pin to"},
                "title": {"type": "string", "description": "Pin title (max 100 characters)"},
                "description": {"type": "string", "description": "Pin description (max 500 characters)"},
                "link": {"type": "string", "description": "Destination URL when pin is clicked"},
                "media_source_url": {"type": "string", "description": "Image URL for the pin"},
                "alt_text": {"type": "string", "description": "Alt text for the pin image"},
            },
            "required": ["board_id", "media_source_url"],
        },
    },
    {
        "name": "pinterest_list_pins",
        "description": "List pins on a specific Pinterest board",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string", "description": "Pinterest board ID"},
                "page_size": {"type": "integer", "description": "Number of pins per page (max 250)", "default": 25},
                "bookmark": {"type": "string", "description": "Pagination cursor"},
            },
            "required": ["board_id"],
        },
    },
    {
        "name": "pinterest_get_board_analytics",
        "description": "Get analytics data for a Pinterest board",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string", "description": "Pinterest board ID"},
                "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
                "metric_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Metrics: IMPRESSION, ENGAGEMENTS, OUTBOUND_CLICKS, PIN_CLICK, SAVE",
                },
            },
            "required": ["board_id", "start_date", "end_date"],
        },
    },
    {
        "name": "pinterest_search_pins",
        "description": "Search for pins matching a query on Pinterest",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for pins"},
                "scope": {"type": "string", "description": "Search scope: my_pins, my_boards", "default": "my_pins"},
                "page_size": {"type": "integer", "description": "Number of results per page (max 250)", "default": 25},
                "bookmark": {"type": "string", "description": "Pagination cursor"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "pinterest_create_board",
        "description": "Create a new Pinterest board",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Board name"},
                "description": {"type": "string", "description": "Board description"},
                "privacy": {"type": "string", "description": "Privacy setting: PUBLIC, PROTECTED, SECRET", "default": "PUBLIC"},
            },
            "required": ["name"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("PINTEREST_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "PINTEREST_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "pinterest_list_boards":
                params: dict[str, Any] = {
                    "page_size": arguments.get("page_size", 25),
                }
                if "bookmark" in arguments:
                    params["bookmark"] = arguments["bookmark"]
                if "privacy" in arguments:
                    params["privacy"] = arguments["privacy"]
                r = await client.get(f"{BASE}/boards", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "boards": [
                        {
                            "id": b.get("id"),
                            "name": b.get("name"),
                            "description": b.get("description"),
                            "privacy": b.get("privacy"),
                            "pin_count": b.get("pin_count"),
                        }
                        for b in data.get("items", [])
                    ],
                    "bookmark": data.get("bookmark"),
                }

            elif tool_name == "pinterest_create_pin":
                payload: dict[str, Any] = {
                    "board_id": arguments["board_id"],
                    "media_source": {
                        "source_type": "image_url",
                        "url": arguments["media_source_url"],
                    },
                }
                if "title" in arguments:
                    payload["title"] = arguments["title"]
                if "description" in arguments:
                    payload["description"] = arguments["description"]
                if "link" in arguments:
                    payload["link"] = arguments["link"]
                if "alt_text" in arguments:
                    payload["alt_text"] = arguments["alt_text"]
                r = await client.post(f"{BASE}/pins", headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "board_id": data.get("board_id"),
                    "title": data.get("title"),
                    "link": data.get("link"),
                }

            elif tool_name == "pinterest_list_pins":
                params = {
                    "page_size": arguments.get("page_size", 25),
                }
                if "bookmark" in arguments:
                    params["bookmark"] = arguments["bookmark"]
                r = await client.get(
                    f"{BASE}/boards/{arguments['board_id']}/pins",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "pins": [
                        {
                            "id": p.get("id"),
                            "title": p.get("title"),
                            "description": p.get("description"),
                            "link": p.get("link"),
                            "created_at": p.get("created_at"),
                        }
                        for p in data.get("items", [])
                    ],
                    "bookmark": data.get("bookmark"),
                }

            elif tool_name == "pinterest_get_board_analytics":
                params = {
                    "start_date": arguments["start_date"],
                    "end_date": arguments["end_date"],
                }
                metric_types = arguments.get("metric_types", ["IMPRESSION", "ENGAGEMENTS"])
                params["metric_types"] = ",".join(metric_types)
                r = await client.get(
                    f"{BASE}/boards/{arguments['board_id']}/analytics",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "pinterest_search_pins":
                params = {
                    "query": arguments["query"],
                    "scope": arguments.get("scope", "my_pins"),
                    "page_size": arguments.get("page_size", 25),
                }
                if "bookmark" in arguments:
                    params["bookmark"] = arguments["bookmark"]
                r = await client.get(f"{BASE}/search/pins", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "pins": data.get("items", []),
                    "bookmark": data.get("bookmark"),
                }

            elif tool_name == "pinterest_create_board":
                payload = {
                    "name": arguments["name"],
                    "privacy": arguments.get("privacy", "PUBLIC"),
                }
                if "description" in arguments:
                    payload["description"] = arguments["description"]
                r = await client.post(f"{BASE}/boards", headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "privacy": data.get("privacy"),
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("pinterest_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
