"""WordPress MCP server — content management, posts, pages, and media.

Environment:
  WORDPRESS_URL:              Site URL (e.g. 'https://myblog.com')
  WORDPRESS_USERNAME:         WordPress admin username
  WORDPRESS_APP_PASSWORD:     WordPress Application Password (Settings > User > Application Passwords)
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base() -> str:
    url = os.getenv("WORDPRESS_URL", "").rstrip("/")
    return f"{url}/wp-json/wp/v2"


def _headers() -> dict[str, str]:
    username = os.getenv("WORDPRESS_USERNAME", "")
    app_password = os.getenv("WORDPRESS_APP_PASSWORD", "")
    creds = base64.b64encode(f"{username}:{app_password}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "wordpress_list_posts",
        "description": "List WordPress posts",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "default": 20},
                "page": {"type": "integer", "default": 1},
                "status": {
                    "type": "string",
                    "enum": ["publish", "draft", "pending", "private", "any"],
                    "default": "publish",
                },
                "search": {"type": "string"},
                "categories": {"type": "string", "description": "Comma-separated category IDs"},
                "tags": {"type": "string", "description": "Comma-separated tag IDs"},
                "author": {"type": "integer", "description": "Author user ID"},
            },
        },
    },
    {
        "name": "wordpress_get_post",
        "description": "Get a WordPress post by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {"type": "integer"},
            },
            "required": ["post_id"],
        },
    },
    {
        "name": "wordpress_create_post",
        "description": "Create a new WordPress post",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string", "description": "Post content (HTML/blocks)"},
                "excerpt": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["publish", "draft", "pending", "private"],
                    "default": "draft",
                },
                "categories": {"type": "array", "items": {"type": "integer"}},
                "tags": {"type": "array", "items": {"type": "integer"}},
                "slug": {"type": "string"},
                "featured_media": {"type": "integer"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "wordpress_update_post",
        "description": "Update an existing WordPress post",
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {"type": "integer"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "status": {"type": "string"},
                "categories": {"type": "array", "items": {"type": "integer"}},
                "tags": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["post_id"],
        },
    },
    {
        "name": "wordpress_list_pages",
        "description": "List WordPress pages",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "default": 20},
                "page": {"type": "integer", "default": 1},
                "status": {"type": "string", "default": "publish"},
                "parent": {"type": "integer", "description": "Parent page ID"},
            },
        },
    },
    {
        "name": "wordpress_list_categories",
        "description": "List WordPress categories",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "default": 50},
                "page": {"type": "integer", "default": 1},
                "search": {"type": "string"},
            },
        },
    },
    {
        "name": "wordpress_search",
        "description": "Search WordPress content (posts, pages, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "per_page": {"type": "integer", "default": 20},
                "type": {"type": "string", "default": "post"},
                "subtype": {"type": "string"},
            },
            "required": ["query"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    wp_url = os.getenv("WORDPRESS_URL", "")
    username = os.getenv("WORDPRESS_USERNAME", "")
    app_pass = os.getenv("WORDPRESS_APP_PASSWORD", "")
    if not all([wp_url, username, app_pass]):
        return {"error": "WORDPRESS_URL, WORDPRESS_USERNAME, and WORDPRESS_APP_PASSWORD must be configured"}

    base = _base()

    try:
        async with httpx.AsyncClient(headers=_headers(), timeout=30.0) as c:
            if tool_name == "wordpress_list_posts":
                params: dict[str, Any] = {
                    "per_page": arguments.get("per_page", 20),
                    "page": arguments.get("page", 1),
                    "status": arguments.get("status", "publish"),
                }
                for key in ["search", "categories", "tags", "author"]:
                    if v := arguments.get(key):
                        params[key] = v
                r = await c.get(f"{base}/posts", params=params)
                r.raise_for_status()
                return {"posts": r.json(), "total": int(r.headers.get("X-WP-Total", 0))}

            elif tool_name == "wordpress_get_post":
                r = await c.get(f"{base}/posts/{arguments['post_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "wordpress_create_post":
                payload: dict[str, Any] = {
                    "title": arguments["title"],
                    "status": arguments.get("status", "draft"),
                }
                for key in ["content", "excerpt", "categories", "tags", "slug", "featured_media"]:
                    if v := arguments.get(key):
                        payload[key] = v
                r = await c.post(f"{base}/posts", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "wordpress_update_post":
                pid = arguments["post_id"]
                payload = {}
                for key in ["title", "content", "status", "categories", "tags"]:
                    if v := arguments.get(key):
                        payload[key] = v
                r = await c.post(f"{base}/posts/{pid}", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "wordpress_list_pages":
                params = {
                    "per_page": arguments.get("per_page", 20),
                    "page": arguments.get("page", 1),
                    "status": arguments.get("status", "publish"),
                }
                if parent := arguments.get("parent"):
                    params["parent"] = parent
                r = await c.get(f"{base}/pages", params=params)
                r.raise_for_status()
                return {"pages": r.json()}

            elif tool_name == "wordpress_list_categories":
                r = await c.get(
                    f"{base}/categories",
                    params={
                        "per_page": arguments.get("per_page", 50),
                        "page": arguments.get("page", 1),
                        **({"search": arguments["search"]} if arguments.get("search") else {}),
                    },
                )
                r.raise_for_status()
                return {"categories": r.json()}

            elif tool_name == "wordpress_search":
                r = await c.get(
                    f"{base}/search",
                    params={
                        "search": arguments["query"],
                        "per_page": arguments.get("per_page", 20),
                        "type": arguments.get("type", "post"),
                        **({"subtype": arguments["subtype"]} if arguments.get("subtype") else {}),
                    },
                )
                r.raise_for_status()
                return {"results": r.json()}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("wordpress_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
