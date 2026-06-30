"""Storyblok MCP server — Storyblok headless CMS stories, components, and publishing.

Environment:
  STORYBLOK_ACCESS_TOKEN: Storyblok Management API OAuth2 token or personal access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://mapi.storyblok.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "storyblok_list_stories",
        "description": "List stories (content entries) in a Storyblok space",
        "parameters": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string", "description": "Storyblok space ID"},
                "page": {"type": "integer", "description": "Page number for pagination", "default": 1},
                "per_page": {"type": "integer", "description": "Number of stories per page (max 100)", "default": 25},
                "starts_with": {"type": "string", "description": "Filter stories by slug prefix"},
                "content_type": {"type": "string", "description": "Filter by content type/component name"},
            },
            "required": ["space_id"],
        },
    },
    {
        "name": "storyblok_get_story",
        "description": "Get details of a specific Storyblok story by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string", "description": "Storyblok space ID"},
                "story_id": {"type": "string", "description": "Storyblok story ID or slug"},
            },
            "required": ["space_id", "story_id"],
        },
    },
    {
        "name": "storyblok_create_story",
        "description": "Create a new story in a Storyblok space",
        "parameters": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string", "description": "Storyblok space ID"},
                "name": {"type": "string", "description": "Story display name"},
                "slug": {"type": "string", "description": "URL-friendly slug for the story"},
                "content": {"type": "object", "description": "Story content object (component fields)"},
                "parent_id": {"type": "integer", "description": "Parent folder ID"},
            },
            "required": ["space_id", "name", "slug", "content"],
        },
    },
    {
        "name": "storyblok_update_story",
        "description": "Update an existing Storyblok story",
        "parameters": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string", "description": "Storyblok space ID"},
                "story_id": {"type": "string", "description": "Storyblok story ID"},
                "name": {"type": "string", "description": "Updated story name"},
                "content": {"type": "object", "description": "Updated content object"},
                "slug": {"type": "string", "description": "Updated slug"},
            },
            "required": ["space_id", "story_id"],
        },
    },
    {
        "name": "storyblok_list_components",
        "description": "List all components (content types) defined in a Storyblok space",
        "parameters": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string", "description": "Storyblok space ID"},
            },
            "required": ["space_id"],
        },
    },
    {
        "name": "storyblok_publish_story",
        "description": "Publish a draft story in Storyblok to make it live",
        "parameters": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string", "description": "Storyblok space ID"},
                "story_id": {"type": "string", "description": "Story ID to publish"},
                "release_id": {"type": "integer", "description": "Release ID to publish to (optional)"},
            },
            "required": ["space_id", "story_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("STORYBLOK_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "STORYBLOK_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "storyblok_list_stories":
                space_id = arguments["space_id"]
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                }
                if "starts_with" in arguments:
                    params["starts_with"] = arguments["starts_with"]
                if "content_type" in arguments:
                    params["content_type"] = arguments["content_type"]
                r = await client.get(f"{BASE}/spaces/{space_id}/stories", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "stories": [
                        {
                            "id": s.get("id"),
                            "name": s.get("name"),
                            "slug": s.get("slug"),
                            "full_slug": s.get("full_slug"),
                            "published": s.get("published"),
                            "created_at": s.get("created_at"),
                        }
                        for s in data.get("stories", [])
                    ],
                    "total": data.get("total", 0),
                }

            elif tool_name == "storyblok_get_story":
                r = await client.get(
                    f"{BASE}/spaces/{arguments['space_id']}/stories/{arguments['story_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json().get("story", r.json())

            elif tool_name == "storyblok_create_story":
                payload: dict[str, Any] = {
                    "story": {
                        "name": arguments["name"],
                        "slug": arguments["slug"],
                        "content": arguments["content"],
                    }
                }
                if "parent_id" in arguments:
                    payload["story"]["parent_id"] = arguments["parent_id"]
                r = await client.post(
                    f"{BASE}/spaces/{arguments['space_id']}/stories",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
                story = data.get("story", {})
                return {"id": story.get("id"), "name": story.get("name"), "slug": story.get("slug")}

            elif tool_name == "storyblok_update_story":
                story_payload: dict[str, Any] = {}
                for field in ("name", "content", "slug"):
                    if field in arguments:
                        story_payload[field] = arguments[field]
                r = await client.put(
                    f"{BASE}/spaces/{arguments['space_id']}/stories/{arguments['story_id']}",
                    headers=headers,
                    json={"story": story_payload},
                )
                r.raise_for_status()
                data = r.json()
                return {"updated": True, "story": data.get("story", {})}

            elif tool_name == "storyblok_list_components":
                r = await client.get(
                    f"{BASE}/spaces/{arguments['space_id']}/components",
                    headers=headers,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "components": [
                        {
                            "id": c.get("id"),
                            "name": c.get("name"),
                            "display_name": c.get("display_name"),
                        }
                        for c in data.get("components", [])
                    ],
                }

            elif tool_name == "storyblok_publish_story":
                params = {}
                if "release_id" in arguments:
                    params["release_id"] = arguments["release_id"]
                r = await client.get(
                    f"{BASE}/spaces/{arguments['space_id']}/stories/{arguments['story_id']}/publish",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return {"published": True, "story_id": arguments["story_id"]}

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("storyblok_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
