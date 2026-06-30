"""Substack MCP server — Substack newsletter posts, subscribers, stats, and email sends.

Environment:
  SUBSTACK_API_KEY: Substack API key (from publication settings)
  SUBSTACK_PUBLICATION: Substack publication subdomain (e.g. mypublication)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "substack_list_posts",
        "description": "List posts in the Substack publication",
        "parameters": {
            "type": "object",
            "properties": {
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
                "limit": {"type": "integer", "description": "Number of posts to return", "default": 25},
                "type": {"type": "string", "description": "Post type: post, podcast, thread"},
            },
        },
    },
    {
        "name": "substack_create_post",
        "description": "Create a new draft post in the Substack publication",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Post title"},
                "body": {"type": "string", "description": "Post body content (HTML or Markdown)"},
                "subtitle": {"type": "string", "description": "Post subtitle/description"},
                "is_free": {"type": "boolean", "description": "Whether the post is free for all subscribers", "default": True},
                "audience": {"type": "string", "description": "Audience: everyone, only_paid, only_free"},
            },
            "required": ["title", "body"],
        },
    },
    {
        "name": "substack_publish_post",
        "description": "Publish a draft Substack post immediately or schedule it",
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string", "description": "Post ID to publish"},
                "send_email": {"type": "boolean", "description": "Send email notification to subscribers", "default": True},
                "scheduled_at": {"type": "string", "description": "ISO 8601 scheduled publish date (omit for immediate)"},
            },
            "required": ["post_id"],
        },
    },
    {
        "name": "substack_list_subscribers",
        "description": "List subscribers of the Substack publication",
        "parameters": {
            "type": "object",
            "properties": {
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
                "limit": {"type": "integer", "description": "Number of subscribers per page", "default": 25},
                "type": {"type": "string", "description": "Subscription type: free, paid, comp, gift"},
            },
        },
    },
    {
        "name": "substack_get_stats",
        "description": "Get publication statistics for the Substack newsletter",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "substack_send_email",
        "description": "Send an email to Substack subscribers (blast or targeted)",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Email body content (HTML)"},
                "audience": {"type": "string", "description": "Audience: everyone, only_paid, only_free"},
                "from_name": {"type": "string", "description": "Sender display name"},
            },
            "required": ["subject", "body"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("SUBSTACK_API_KEY", "")
    publication = os.getenv("SUBSTACK_PUBLICATION", "")
    if not api_key:
        return {"error": "SUBSTACK_API_KEY not configured"}
    if not publication:
        return {"error": "SUBSTACK_PUBLICATION not configured"}

    base = f"https://substack.com/api/v1"
    pub_base = f"https://{publication}.substack.com/api/v1"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "substack_list_posts":
                params: dict[str, Any] = {
                    "offset": arguments.get("offset", 0),
                    "limit": arguments.get("limit", 25),
                }
                if "type" in arguments:
                    params["type"] = arguments["type"]
                r = await client.get(f"{pub_base}/posts", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                posts = data if isinstance(data, list) else data.get("posts", [])
                return {
                    "posts": [
                        {
                            "id": p.get("id"),
                            "title": p.get("title"),
                            "slug": p.get("slug"),
                            "type": p.get("type"),
                            "audience": p.get("audience"),
                            "published_at": p.get("post_date"),
                        }
                        for p in posts
                    ],
                }

            elif tool_name == "substack_create_post":
                payload: dict[str, Any] = {
                    "title": arguments["title"],
                    "body": arguments["body"],
                    "draft": True,
                }
                for field in ("subtitle", "is_free", "audience"):
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await client.post(f"{base}/drafts", headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "slug": data.get("slug"),
                    "draft": data.get("draft"),
                }

            elif tool_name == "substack_publish_post":
                payload = {
                    "send_email": arguments.get("send_email", True),
                }
                if "scheduled_at" in arguments:
                    payload["scheduled_at"] = arguments["scheduled_at"]
                r = await client.post(
                    f"{base}/posts/{arguments['post_id']}/publish",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return {"published": True, "post_id": arguments["post_id"]}

            elif tool_name == "substack_list_subscribers":
                params = {
                    "offset": arguments.get("offset", 0),
                    "limit": arguments.get("limit", 25),
                }
                if "type" in arguments:
                    params["type"] = arguments["type"]
                r = await client.get(f"{base}/subscriptions", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                subs = data if isinstance(data, list) else data.get("subscriptions", [])
                return {
                    "subscribers": [
                        {
                            "id": s.get("id"),
                            "email": s.get("email"),
                            "type": s.get("type"),
                            "created_at": s.get("created_at"),
                        }
                        for s in subs
                    ],
                }

            elif tool_name == "substack_get_stats":
                r = await client.get(f"{pub_base}/publication/stats", headers=headers)
                r.raise_for_status()
                return r.json()

            elif tool_name == "substack_send_email":
                payload = {
                    "subject": arguments["subject"],
                    "body": arguments["body"],
                    "audience": arguments.get("audience", "everyone"),
                }
                if "from_name" in arguments:
                    payload["from_name"] = arguments["from_name"]
                r = await client.post(f"{base}/emails", headers=headers, json=payload)
                r.raise_for_status()
                return {"sent": True, "subject": arguments["subject"]}

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("substack_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
