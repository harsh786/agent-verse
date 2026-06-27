"""Notion MCP server — Notion REST API v1 integration.

Environment variables:
  NOTION_API_KEY: Notion integration token (secret_...)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

NOTION_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

TOOL_DEFINITIONS = [
    {
        "name": "notion_search",
        "description": "Search Notion pages and databases by title",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
                "filter_type": {
                    "type": "string",
                    "enum": ["page", "database"],
                    "description": "Limit results to pages or databases",
                },
                "page_size": {"type": "integer", "default": 25},
                "start_cursor": {"type": "string", "description": "Pagination cursor"},
            },
        },
    },
    {
        "name": "notion_get_page",
        "description": "Retrieve a Notion page by its ID",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Notion page UUID (with or without dashes)"},
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "notion_create_page",
        "description": "Create a new Notion page inside a parent page or database",
        "parameters": {
            "type": "object",
            "properties": {
                "parent_type": {
                    "type": "string",
                    "enum": ["page_id", "database_id"],
                    "default": "page_id",
                },
                "parent_id": {"type": "string", "description": "Parent page ID or database ID"},
                "title": {"type": "string"},
                "content": {
                    "type": "string",
                    "description": "Plain-text content added as a paragraph block",
                    "default": "",
                },
                "properties": {
                    "type": "object",
                    "description": "Database properties as a JSON object (for database pages)",
                },
            },
            "required": ["parent_id", "title"],
        },
    },
    {
        "name": "notion_update_page",
        "description": "Update properties of a Notion page (archive, title, or database properties)",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "archived": {"type": "boolean"},
                "title": {"type": "string", "description": "New title (for plain pages)"},
                "properties": {
                    "type": "object",
                    "description": "Properties to update (for database pages)",
                },
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "notion_get_database",
        "description": "Retrieve a Notion database schema and metadata",
        "parameters": {
            "type": "object",
            "properties": {
                "database_id": {"type": "string"},
            },
            "required": ["database_id"],
        },
    },
    {
        "name": "notion_query_database",
        "description": "Query rows from a Notion database with optional filters and sorts",
        "parameters": {
            "type": "object",
            "properties": {
                "database_id": {"type": "string"},
                "filter": {
                    "type": "object",
                    "description": "Notion filter object (see Notion API docs)",
                },
                "sorts": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of sort objects",
                },
                "page_size": {"type": "integer", "default": 50},
                "start_cursor": {"type": "string"},
            },
            "required": ["database_id"],
        },
    },
    {
        "name": "notion_create_database",
        "description": "Create a new Notion database as a child of a page",
        "parameters": {
            "type": "object",
            "properties": {
                "parent_page_id": {"type": "string"},
                "title": {"type": "string"},
                "properties": {
                    "type": "object",
                    "description": "Database property schema object (Notion API format)",
                },
            },
            "required": ["parent_page_id", "title", "properties"],
        },
    },
    {
        "name": "notion_append_blocks",
        "description": "Append content blocks to a Notion page or block",
        "parameters": {
            "type": "object",
            "properties": {
                "block_id": {
                    "type": "string",
                    "description": "Page ID or block ID to append children to",
                },
                "children": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of Notion block objects",
                },
            },
            "required": ["block_id", "children"],
        },
    },
]


def _notion_headers() -> dict[str, str]:
    token = os.getenv("NOTION_API_KEY", "")
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _format_page(page: dict) -> dict:
    """Extract a clean summary from a raw Notion page object."""
    props = page.get("properties", {})
    title_prop = props.get("title") or props.get("Name") or {}
    title_items = title_prop.get("title", []) or title_prop.get("rich_text", [])
    title = "".join(t.get("plain_text", "") for t in title_items)
    return {
        "id": page.get("id", ""),
        "title": title,
        "url": page.get("url", ""),
        "created_time": page.get("created_time", ""),
        "last_edited_time": page.get("last_edited_time", ""),
        "archived": page.get("archived", False),
        "parent_type": list(page.get("parent", {}).keys())[0] if page.get("parent") else "",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("NOTION_API_KEY", "")
    if not token:
        return {"error": "NOTION_API_KEY not configured"}

    async with httpx.AsyncClient(
        base_url=NOTION_BASE, headers=_notion_headers(), timeout=30.0
    ) as client:
        if tool_name == "notion_search":
            payload: dict[str, Any] = {"page_size": arguments.get("page_size", 25)}
            if arguments.get("query"):
                payload["query"] = arguments["query"]
            if arguments.get("filter_type"):
                payload["filter"] = {"value": arguments["filter_type"], "property": "object"}
            if arguments.get("start_cursor"):
                payload["start_cursor"] = arguments["start_cursor"]

            resp = await client.post("/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "results": [_format_page(r) for r in data.get("results", [])],
                "has_more": data.get("has_more", False),
                "next_cursor": data.get("next_cursor"),
            }

        elif tool_name == "notion_get_page":
            resp = await client.get(f"/pages/{arguments['page_id']}")
            resp.raise_for_status()
            return {"page": _format_page(resp.json())}

        elif tool_name == "notion_create_page":
            parent_type = arguments.get("parent_type", "page_id")
            payload: dict[str, Any] = {
                "parent": {parent_type: arguments["parent_id"]},
                "properties": {
                    "title": {
                        "title": [{"type": "text", "text": {"content": arguments["title"]}}]
                    }
                },
            }
            # Merge custom properties if provided
            if arguments.get("properties"):
                payload["properties"].update(arguments["properties"])

            if arguments.get("content"):
                payload["children"] = [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {"type": "text", "text": {"content": arguments["content"]}}
                            ]
                        },
                    }
                ]

            resp = await client.post("/pages", json=payload)
            resp.raise_for_status()
            return {"page": _format_page(resp.json())}

        elif tool_name == "notion_update_page":
            page_id = arguments["page_id"]
            payload: dict[str, Any] = {}
            if "archived" in arguments:
                payload["archived"] = arguments["archived"]
            if arguments.get("title"):
                payload["properties"] = {
                    "title": {
                        "title": [{"type": "text", "text": {"content": arguments["title"]}}]
                    }
                }
            if arguments.get("properties"):
                payload.setdefault("properties", {}).update(arguments["properties"])

            resp = await client.patch(f"/pages/{page_id}", json=payload)
            resp.raise_for_status()
            return {"page": _format_page(resp.json())}

        elif tool_name == "notion_get_database":
            resp = await client.get(f"/databases/{arguments['database_id']}")
            resp.raise_for_status()
            data = resp.json()
            title = "".join(
                t.get("plain_text", "") for t in data.get("title", [])
            )
            return {
                "id": data.get("id", ""),
                "title": title,
                "created_time": data.get("created_time", ""),
                "last_edited_time": data.get("last_edited_time", ""),
                "properties": {k: v.get("type", "") for k, v in data.get("properties", {}).items()},
            }

        elif tool_name == "notion_query_database":
            db_id = arguments["database_id"]
            payload = {"page_size": arguments.get("page_size", 50)}
            if arguments.get("filter"):
                payload["filter"] = arguments["filter"]
            if arguments.get("sorts"):
                payload["sorts"] = arguments["sorts"]
            if arguments.get("start_cursor"):
                payload["start_cursor"] = arguments["start_cursor"]

            resp = await client.post(f"/databases/{db_id}/query", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "results": [_format_page(r) for r in data.get("results", [])],
                "has_more": data.get("has_more", False),
                "next_cursor": data.get("next_cursor"),
            }

        elif tool_name == "notion_create_database":
            payload = {
                "parent": {"page_id": arguments["parent_page_id"]},
                "title": [{"type": "text", "text": {"content": arguments["title"]}}],
                "properties": arguments["properties"],
            }
            resp = await client.post("/databases", json=payload)
            resp.raise_for_status()
            data = resp.json()
            title = "".join(t.get("plain_text", "") for t in data.get("title", []))
            return {"id": data.get("id", ""), "title": title, "url": data.get("url", "")}

        elif tool_name == "notion_append_blocks":
            block_id = arguments["block_id"]
            payload = {"children": arguments["children"]}
            resp = await client.patch(f"/blocks/{block_id}/children", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "appended": len(data.get("results", [])),
                "results": data.get("results", []),
            }

        else:
            return {"error": f"Unknown tool: {tool_name}"}
