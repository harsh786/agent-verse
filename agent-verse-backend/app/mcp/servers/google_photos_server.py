"""Google Photos MCP server — manage photo library, albums, and media items.

Environment:
  GOOGLE_ACCESS_TOKEN: OAuth2 access token with photoslibrary scope
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://photoslibrary.googleapis.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "google_photos_list_media_items",
        "description": "List all media items (photos and videos) in the user's Google Photos library",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "description": "Number of media items per page (max 100)"},
                "page_token": {"type": "string", "description": "Pagination token from previous response"},
            },
        },
    },
    {
        "name": "google_photos_create_album",
        "description": "Create a new album in the user's Google Photos library",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title of the new album"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "google_photos_list_albums",
        "description": "List all albums in the user's Google Photos library",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "description": "Albums per page (max 50)"},
                "page_token": {"type": "string", "description": "Pagination token"},
                "exclude_non_app_created_data": {"type": "boolean", "description": "If true, exclude albums not created by the app"},
            },
        },
    },
    {
        "name": "google_photos_search_media",
        "description": "Search for media items using content categories, date ranges, or text",
        "parameters": {
            "type": "object",
            "properties": {
                "album_id": {"type": "string", "description": "Filter by album ID"},
                "content_categories": {
                    "type": "array",
                    "description": "Content category filters (ANIMALS, FOOD, LANDSCAPES, etc.)",
                    "items": {"type": "string"},
                },
                "date_from": {"type": "string", "description": "Start date filter in YYYY-MM-DD format"},
                "date_to": {"type": "string", "description": "End date filter in YYYY-MM-DD format"},
                "page_size": {"type": "integer", "description": "Results per page"},
                "page_token": {"type": "string", "description": "Pagination token"},
            },
        },
    },
    {
        "name": "google_photos_add_to_album",
        "description": "Add existing media items to an album",
        "parameters": {
            "type": "object",
            "properties": {
                "album_id": {"type": "string", "description": "ID of the album to add items to"},
                "media_item_ids": {
                    "type": "array",
                    "description": "List of media item IDs to add",
                    "items": {"type": "string"},
                },
            },
            "required": ["album_id", "media_item_ids"],
        },
    },
    {
        "name": "google_photos_share_album",
        "description": "Share a Google Photos album to generate a shareable link",
        "parameters": {
            "type": "object",
            "properties": {
                "album_id": {"type": "string", "description": "ID of the album to share"},
                "is_collaborative": {"type": "boolean", "description": "Allow collaborators to add photos"},
                "is_commentable": {"type": "boolean", "description": "Allow collaborators to add comments"},
            },
            "required": ["album_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "GOOGLE_ACCESS_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "google_photos_list_media_items":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/mediaItems", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "google_photos_create_album":
                r = await client.post(
                    f"{BASE_URL}/albums",
                    headers=headers,
                    json={"album": {"title": arguments["title"]}},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "google_photos_list_albums":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/albums", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "google_photos_search_media":
                payload: dict[str, Any] = {}
                if "album_id" in arguments:
                    payload["albumId"] = arguments["album_id"]
                if "page_size" in arguments:
                    payload["pageSize"] = arguments["page_size"]
                if "page_token" in arguments:
                    payload["pageToken"] = arguments["page_token"]
                filters: dict[str, Any] = {}
                if "content_categories" in arguments:
                    filters["contentFilter"] = {"includedContentCategories": arguments["content_categories"]}
                if "date_from" in arguments or "date_to" in arguments:
                    date_parts_from = arguments.get("date_from", "2000-01-01").split("-")
                    date_parts_to = arguments.get("date_to", "2099-12-31").split("-")
                    filters["dateFilter"] = {
                        "ranges": [{
                            "startDate": {"year": int(date_parts_from[0]), "month": int(date_parts_from[1]), "day": int(date_parts_from[2])},
                            "endDate": {"year": int(date_parts_to[0]), "month": int(date_parts_to[1]), "day": int(date_parts_to[2])},
                        }]
                    }
                if filters:
                    payload["filters"] = filters
                r = await client.post(f"{BASE_URL}/mediaItems:search", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "google_photos_add_to_album":
                r = await client.post(
                    f"{BASE_URL}/albums/{arguments['album_id']}:addEnrichment",
                    headers=headers,
                    json={"mediaItemIds": arguments["media_item_ids"]},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "google_photos_share_album":
                r = await client.post(
                    f"{BASE_URL}/albums/{arguments['album_id']}:share",
                    headers=headers,
                    json={
                        "sharedAlbumOptions": {
                            "isCollaborative": arguments.get("is_collaborative", False),
                            "isCommentable": arguments.get("is_commentable", False),
                        }
                    },
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
