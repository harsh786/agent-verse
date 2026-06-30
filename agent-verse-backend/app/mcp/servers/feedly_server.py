"""Feedly MCP server — news aggregation, RSS stream reading, and article management.

Environment:
  FEEDLY_ACCESS_TOKEN: Feedly OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://cloud.feedly.com/v3"

TOOL_DEFINITIONS = [
    {
        "name": "feedly_list_streams",
        "description": "List all feed streams and categories in the Feedly account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "feedly_list_entries",
        "description": "List articles from a Feedly stream (feed or category)",
        "parameters": {
            "type": "object",
            "properties": {
                "stream_id": {"type": "string", "description": "Stream ID (feed URL or category ID)"},
                "count": {"type": "integer", "description": "Number of articles to return (max 250)"},
                "ranked": {"type": "string", "description": "Sort order: newest or oldest"},
                "unread_only": {"type": "boolean", "description": "Only return unread articles"},
                "continuation": {"type": "string", "description": "Pagination token"},
            },
            "required": ["stream_id"],
        },
    },
    {
        "name": "feedly_search_feeds",
        "description": "Search for RSS feeds or Feedly sources by keyword or URL",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for feed discovery"},
                "count": {"type": "integer", "description": "Maximum feeds to return"},
                "locale": {"type": "string", "description": "Language/locale filter"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "feedly_get_entry",
        "description": "Get the full content of a specific Feedly article by entry ID",
        "parameters": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "Feedly entry ID"},
            },
            "required": ["entry_id"],
        },
    },
    {
        "name": "feedly_mark_as_read",
        "description": "Mark articles as read in a Feedly stream or by entry IDs",
        "parameters": {
            "type": "object",
            "properties": {
                "entry_ids": {
                    "type": "array",
                    "description": "List of entry IDs to mark as read",
                    "items": {"type": "string"},
                },
                "stream_id": {"type": "string", "description": "Mark all entries in a stream as read"},
                "last_read_entry_id": {"type": "string", "description": "Mark all entries up to this ID as read"},
            },
        },
    },
    {
        "name": "feedly_save_entry",
        "description": "Save an article to the Feedly reading list",
        "parameters": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "Entry ID to save to reading list"},
            },
            "required": ["entry_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("FEEDLY_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "FEEDLY_ACCESS_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "feedly_list_streams":
                r = await client.get(f"{BASE_URL}/subscriptions", headers=headers)
                r.raise_for_status()
                return r.json()

            if tool_name == "feedly_list_entries":
                params: dict[str, Any] = {"streamId": arguments["stream_id"]}
                if "count" in arguments:
                    params["count"] = arguments["count"]
                if "ranked" in arguments:
                    params["ranked"] = arguments["ranked"]
                if "unread_only" in arguments:
                    params["unreadOnly"] = str(arguments["unread_only"]).lower()
                if "continuation" in arguments:
                    params["continuation"] = arguments["continuation"]
                r = await client.get(f"{BASE_URL}/streams/contents", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "feedly_search_feeds":
                params = {"query": arguments["query"]}
                if "count" in arguments:
                    params["count"] = arguments["count"]
                if "locale" in arguments:
                    params["locale"] = arguments["locale"]
                r = await client.get(f"{BASE_URL}/search/feeds", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "feedly_get_entry":
                r = await client.get(
                    f"{BASE_URL}/entries/{arguments['entry_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "feedly_mark_as_read":
                payload: dict[str, Any] = {"action": "markAsRead", "type": "entries"}
                if "entry_ids" in arguments:
                    payload["entryIds"] = arguments["entry_ids"]
                elif "stream_id" in arguments:
                    payload["type"] = "feeds"
                    payload["feedIds"] = [arguments["stream_id"]]
                    if "last_read_entry_id" in arguments:
                        payload["lastReadEntryId"] = arguments["last_read_entry_id"]
                r = await client.post(f"{BASE_URL}/markers", headers=headers, json=payload)
                r.raise_for_status()
                return {"marked_read": True}

            if tool_name == "feedly_save_entry":
                r = await client.put(
                    f"{BASE_URL}/tags/user/-/tag/global.saved",
                    headers=headers,
                    json={"entryIds": [arguments["entry_id"]]},
                )
                r.raise_for_status()
                return {"saved": True}

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
