"""Microsoft OneNote MCP server — notebook and page management via Microsoft Graph API.

Environment:
  MICROSOFT_ACCESS_TOKEN: Microsoft OAuth2 access token with Notes.ReadWrite scope
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

GRAPH_ONENOTE = "https://graph.microsoft.com/v1.0/me/onenote"

TOOL_DEFINITIONS = [
    {
        "name": "onenote_list_notebooks",
        "description": "List all OneNote notebooks",
        "parameters": {
            "type": "object",
            "properties": {
                "order_by": {
                    "type": "string",
                    "default": "lastModifiedDateTime desc",
                },
                "top": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "onenote_list_sections",
        "description": "List sections in a OneNote notebook",
        "parameters": {
            "type": "object",
            "properties": {
                "notebook_id": {"type": "string"},
            },
            "required": ["notebook_id"],
        },
    },
    {
        "name": "onenote_list_pages",
        "description": "List pages in a OneNote section",
        "parameters": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string"},
                "top": {"type": "integer", "default": 20},
                "order_by": {"type": "string", "default": "lastModifiedDateTime desc"},
            },
            "required": ["section_id"],
        },
    },
    {
        "name": "onenote_create_page",
        "description": "Create a new page in a OneNote section",
        "parameters": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string", "description": "HTML body content for the page"},
            },
            "required": ["section_id", "title"],
        },
    },
    {
        "name": "onenote_get_page_content",
        "description": "Get the HTML content of a specific OneNote page",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "onenote_search_pages",
        "description": "Search across OneNote pages",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("MICROSOFT_ACCESS_TOKEN", "")
    if not token:
        return {"error": "MICROSOFT_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "onenote_list_notebooks":
                r = await c.get(
                    f"{GRAPH_ONENOTE}/notebooks",
                    headers=hdrs,
                    params={
                        "$top": arguments.get("top", 20),
                        "$orderby": arguments.get("order_by", "lastModifiedDateTime desc"),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "notebooks": [
                        {
                            "id": nb.get("id"),
                            "display_name": nb.get("displayName"),
                            "last_modified": nb.get("lastModifiedDateTime"),
                        }
                        for nb in data.get("value", [])
                    ]
                }

            elif tool_name == "onenote_list_sections":
                r = await c.get(
                    f"{GRAPH_ONENOTE}/notebooks/{arguments['notebook_id']}/sections",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "sections": [
                        {
                            "id": sec.get("id"),
                            "display_name": sec.get("displayName"),
                            "pages_url": sec.get("pagesUrl"),
                        }
                        for sec in data.get("value", [])
                    ]
                }

            elif tool_name == "onenote_list_pages":
                r = await c.get(
                    f"{GRAPH_ONENOTE}/sections/{arguments['section_id']}/pages",
                    headers=hdrs,
                    params={
                        "$top": arguments.get("top", 20),
                        "$orderby": arguments.get("order_by", "lastModifiedDateTime desc"),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "pages": [
                        {
                            "id": p.get("id"),
                            "title": p.get("title"),
                            "last_modified": p.get("lastModifiedDateTime"),
                            "content_url": p.get("contentUrl"),
                        }
                        for p in data.get("value", [])
                    ]
                }

            elif tool_name == "onenote_create_page":
                section_id = arguments["section_id"]
                title = arguments["title"]
                content = arguments.get("content", "")
                html_body = f"""<!DOCTYPE html>
<html>
  <head><title>{title}</title></head>
  <body>
    <h1>{title}</h1>
    <p>{content}</p>
  </body>
</html>"""
                r = await c.post(
                    f"{GRAPH_ONENOTE}/sections/{section_id}/pages",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "text/html",
                    },
                    content=html_body.encode(),
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "created": True,
                }

            elif tool_name == "onenote_get_page_content":
                r = await c.get(
                    f"{GRAPH_ONENOTE}/pages/{arguments['page_id']}/content",
                    headers={**hdrs, "Accept": "text/html"},
                )
                r.raise_for_status()
                return {
                    "page_id": arguments["page_id"],
                    "content": r.text,
                    "content_type": "text/html",
                }

            elif tool_name == "onenote_search_pages":
                r = await c.get(
                    f"{GRAPH_ONENOTE}/pages",
                    headers=hdrs,
                    params={
                        "$search": arguments["query"],
                        "$top": arguments.get("top", 20),
                        "$select": "id,title,lastModifiedDateTime,parentSection",
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "pages": [
                        {
                            "id": p.get("id"),
                            "title": p.get("title"),
                            "last_modified": p.get("lastModifiedDateTime"),
                            "section": p.get("parentSection", {}).get("displayName"),
                        }
                        for p in data.get("value", [])
                    ]
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("onenote_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
