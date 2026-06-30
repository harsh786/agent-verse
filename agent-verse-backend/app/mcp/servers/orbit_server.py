"""Orbit MCP server — community management: members, activities, and workspace analytics.

Environment variables:
  ORBIT_API_KEY: Orbit API key
  ORBIT_WORKSPACE: Orbit workspace slug (e.g. 'mycompany')
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

ORBIT_BASE = "https://app.orbit.love/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "orbit_list_members",
        "description": "List community members in an Orbit workspace with optional filtering and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "items": {"type": "integer", "description": "Items per page", "default": 25},
                "sort": {
                    "type": "string",
                    "enum": ["orbit_level", "love", "reach", "last_activity_occurred_at"],
                    "default": "orbit_level",
                },
                "direction": {"type": "string", "enum": ["ASC", "DESC"], "default": "DESC"},
                "tags": {"type": "string", "description": "Comma-separated tag slugs to filter by"},
                "query": {"type": "string", "description": "Search query for member name or email"},
            },
        },
    },
    {
        "name": "orbit_add_member",
        "description": "Add a new member to an Orbit workspace community",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Member's full name"},
                "email": {"type": "string", "description": "Member's email address"},
                "github": {"type": "string", "description": "GitHub username"},
                "twitter": {"type": "string", "description": "Twitter handle (without @)"},
                "linkedin": {"type": "string", "description": "LinkedIn profile URL"},
                "tags_to_add": {
                    "type": "string",
                    "description": "Comma-separated tags to apply",
                },
                "bio": {"type": "string"},
            },
        },
    },
    {
        "name": "orbit_update_member",
        "description": "Update an existing Orbit community member by slug",
        "parameters": {
            "type": "object",
            "properties": {
                "member_slug": {"type": "string", "description": "Member's Orbit slug"},
                "name": {"type": "string"},
                "email": {"type": "string"},
                "bio": {"type": "string"},
                "location": {"type": "string"},
                "tags_to_add": {"type": "string", "description": "Tags to add"},
                "tags_to_remove": {"type": "string", "description": "Tags to remove"},
            },
            "required": ["member_slug"],
        },
    },
    {
        "name": "orbit_list_activities",
        "description": "List recent community activities in an Orbit workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "items": {"type": "integer", "default": 25},
                "activity_type": {
                    "type": "string",
                    "description": "Filter by activity type, e.g. 'github_star', 'twitter:mention'",
                },
                "member_slug": {"type": "string", "description": "Filter by member slug"},
            },
        },
    },
    {
        "name": "orbit_track_activity",
        "description": "Track a custom activity for an Orbit community member",
        "parameters": {
            "type": "object",
            "properties": {
                "member_email": {"type": "string", "description": "Member's email to identify them"},
                "activity_type": {"type": "string", "description": "Custom activity type key, e.g. 'product:review'"},
                "title": {"type": "string", "description": "Activity title/description"},
                "occurred_at": {"type": "string", "description": "Activity timestamp (ISO 8601)"},
                "link": {"type": "string", "description": "URL associated with the activity"},
                "weight": {"type": "number", "description": "Activity weight/love score (default 1.0)"},
            },
            "required": ["member_email", "activity_type"],
        },
    },
    {
        "name": "orbit_get_workspace_stats",
        "description": "Get high-level statistics and health metrics for an Orbit workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Stats window start date (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "Stats window end date (YYYY-MM-DD)"},
            },
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("ORBIT_API_KEY", "")
    if not api_key:
        return {"error": "ORBIT_API_KEY not configured"}

    workspace = os.getenv("ORBIT_WORKSPACE", "")
    if not workspace:
        return {"error": "ORBIT_WORKSPACE not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=ORBIT_BASE, headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "orbit_list_members":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "items": arguments.get("items", 25),
                    "sort": arguments.get("sort", "orbit_level"),
                    "direction": arguments.get("direction", "DESC"),
                }
                if "tags" in arguments:
                    params["tags"] = arguments["tags"]
                if "query" in arguments:
                    params["query"] = arguments["query"]
                r = await c.get(f"/{workspace}/members", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "orbit_add_member":
                member: dict[str, Any] = {}
                for k in ("name", "email", "github", "twitter", "linkedin", "bio", "tags_to_add"):
                    if k in arguments:
                        member[k] = arguments[k]
                r = await c.post(f"/{workspace}/members", json={"member": member})
                r.raise_for_status()
                return r.json()

            elif tool_name == "orbit_update_member":
                slug = arguments["member_slug"]
                member = {}
                for k in ("name", "email", "bio", "location", "tags_to_add", "tags_to_remove"):
                    if k in arguments:
                        member[k] = arguments[k]
                r = await c.put(f"/{workspace}/members/{slug}", json={"member": member})
                r.raise_for_status()
                return r.json()

            elif tool_name == "orbit_list_activities":
                params = {
                    "page": arguments.get("page", 1),
                    "items": arguments.get("items", 25),
                }
                if "activity_type" in arguments:
                    params["activity_type"] = arguments["activity_type"]
                if "member_slug" in arguments:
                    slug = arguments["member_slug"]
                    r = await c.get(f"/{workspace}/members/{slug}/activities", params=params)
                else:
                    r = await c.get(f"/{workspace}/activities", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "orbit_track_activity":
                body: dict[str, Any] = {
                    "activity": {
                        "activity_type": arguments["activity_type"],
                    },
                    "identity": {
                        "source": "email",
                        "email": arguments["member_email"],
                    },
                }
                if "title" in arguments:
                    body["activity"]["title"] = arguments["title"]
                if "occurred_at" in arguments:
                    body["activity"]["occurred_at"] = arguments["occurred_at"]
                if "link" in arguments:
                    body["activity"]["link"] = arguments["link"]
                if "weight" in arguments:
                    body["activity"]["weight"] = arguments["weight"]
                r = await c.post(f"/{workspace}/activities", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "orbit_get_workspace_stats":
                params = {}
                if "start_date" in arguments:
                    params["start_date"] = arguments["start_date"]
                if "end_date" in arguments:
                    params["end_date"] = arguments["end_date"]
                r = await c.get(f"/{workspace}/stats", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("orbit_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
