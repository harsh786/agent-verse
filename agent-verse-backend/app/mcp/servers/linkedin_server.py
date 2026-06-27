"""LinkedIn MCP server — profile, people/company search, and posting.

Environment variables:
  LINKEDIN_ACCESS_TOKEN: OAuth2 access token with appropriate scopes
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

LINKEDIN_BASE = "https://api.linkedin.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "linkedin_get_profile",
        "description": "Get the authenticated user's LinkedIn profile (me endpoint)",
        "parameters": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "string",
                    "description": "Comma-separated projection fields, e.g. id,firstName,lastName",
                    "default": "id,firstName,lastName,headline",
                },
            },
        },
    },
    {
        "name": "linkedin_search_people",
        "description": "Search LinkedIn people using keywords and optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "keywords": {"type": "string"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "title": {"type": "string"},
                "company": {"type": "string"},
                "count": {"type": "integer", "default": 10},
                "start": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "linkedin_search_companies",
        "description": "Search LinkedIn companies by keywords",
        "parameters": {
            "type": "object",
            "properties": {
                "keywords": {"type": "string"},
                "count": {"type": "integer", "default": 10},
                "start": {"type": "integer", "default": 0},
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "linkedin_create_post",
        "description": "Create a text post on LinkedIn on behalf of the authenticated user",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Post body text"},
                "visibility": {
                    "type": "string",
                    "enum": ["PUBLIC", "CONNECTIONS", "LOGGED_IN"],
                    "default": "PUBLIC",
                },
            },
            "required": ["text"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("LINKEDIN_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "LinkedIn-Version": "202401",
        "X-Restli-Protocol-Version": "2.0.0",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("LINKEDIN_ACCESS_TOKEN", "")
    if not token:
        return {"error": "LINKEDIN_ACCESS_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=LINKEDIN_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "linkedin_get_profile":
                fields = arguments.get("fields", "id,firstName,lastName,headline")
                r = await c.get(f"/me?projection=({fields})")
                r.raise_for_status()
                return r.json()

            elif tool_name == "linkedin_search_people":
                params: dict[str, Any] = {
                    "count": arguments.get("count", 10),
                    "start": arguments.get("start", 0),
                }
                facets: list[str] = []
                if kw := arguments.get("keywords"):
                    params["keywords"] = kw
                if fn := arguments.get("first_name"):
                    facets.append(f"firstName={fn}")
                if ln := arguments.get("last_name"):
                    facets.append(f"lastName={ln}")
                if title := arguments.get("title"):
                    facets.append(f"title={title}")
                if facets:
                    params["facets"] = ",".join(facets)
                r = await c.get("/people-search", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "linkedin_search_companies":
                params = {
                    "keywords": arguments["keywords"],
                    "count": arguments.get("count", 10),
                    "start": arguments.get("start", 0),
                }
                r = await c.get("/organizationAcls", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "linkedin_create_post":
                # UGC Posts API
                # First fetch the author URN
                me_r = await c.get("/me?projection=(id)")
                me_r.raise_for_status()
                author_id = me_r.json().get("id", "")
                payload = {
                    "author": f"urn:li:person:{author_id}",
                    "lifecycleState": "PUBLISHED",
                    "specificContent": {
                        "com.linkedin.ugc.ShareContent": {
                            "shareCommentary": {"text": arguments["text"]},
                            "shareMediaCategory": "NONE",
                        }
                    },
                    "visibility": {
                        "com.linkedin.ugc.MemberNetworkVisibility": arguments.get(
                            "visibility", "PUBLIC"
                        )
                    },
                }
                r = await c.post(
                    "https://api.linkedin.com/v2/ugcPosts", json=payload
                )
                r.raise_for_status()
                return {"post_id": r.headers.get("x-restli-id"), "status": "published"}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("linkedin_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
