"""Webflow MCP server — CMS collections, sites, and publishing.

Environment:
  WEBFLOW_API_TOKEN: Webflow API token
  WEBFLOW_SITE_ID:   Default site ID (optional, can be passed per-call)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

WEBFLOW_BASE = "https://api.webflow.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "webflow_list_sites",
        "description": "List all Webflow sites in the workspace",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "webflow_list_collections",
        "description": "List CMS collections for a Webflow site",
        "parameters": {
            "type": "object",
            "properties": {
                "site_id": {
                    "type": "string",
                    "description": "Site ID (uses WEBFLOW_SITE_ID env var if not provided)",
                },
            },
        },
    },
    {
        "name": "webflow_list_collection_items",
        "description": "List items in a Webflow CMS collection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["collection_id"],
        },
    },
    {
        "name": "webflow_create_collection_item",
        "description": "Create a new item in a Webflow CMS collection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string"},
                "fields": {
                    "type": "object",
                    "description": "Field values for the collection item (name, slug, etc.)",
                },
                "is_draft": {"type": "boolean", "default": False},
            },
            "required": ["collection_id", "fields"],
        },
    },
    {
        "name": "webflow_update_collection_item",
        "description": "Update an existing Webflow CMS collection item",
        "parameters": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string"},
                "item_id": {"type": "string"},
                "fields": {"type": "object"},
                "is_draft": {"type": "boolean"},
            },
            "required": ["collection_id", "item_id", "fields"],
        },
    },
    {
        "name": "webflow_publish_site",
        "description": "Publish a Webflow site to production",
        "parameters": {
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
                "domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Custom domain names to publish to",
                },
            },
        },
    },
    {
        "name": "webflow_get_site",
        "description": "Get details about a Webflow site",
        "parameters": {
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
            },
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("WEBFLOW_API_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _site_id(arguments: dict) -> str:
    return arguments.get("site_id") or os.getenv("WEBFLOW_SITE_ID", "")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("WEBFLOW_API_TOKEN", "")
    if not token:
        return {"error": "WEBFLOW_API_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(base_url=WEBFLOW_BASE, headers=_headers(), timeout=30.0) as c:
            if tool_name == "webflow_list_sites":
                r = await c.get("/sites")
                r.raise_for_status()
                return r.json()

            elif tool_name == "webflow_list_collections":
                sid = _site_id(arguments)
                if not sid:
                    return {"error": "site_id required (or set WEBFLOW_SITE_ID)"}
                r = await c.get(f"/sites/{sid}/collections")
                r.raise_for_status()
                return r.json()

            elif tool_name == "webflow_list_collection_items":
                cid = arguments["collection_id"]
                r = await c.get(
                    f"/collections/{cid}/items",
                    params={
                        "limit": arguments.get("limit", 100),
                        "offset": arguments.get("offset", 0),
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "webflow_create_collection_item":
                cid = arguments["collection_id"]
                payload = {
                    "fieldData": arguments["fields"],
                    "isDraft": arguments.get("is_draft", False),
                }
                r = await c.post(f"/collections/{cid}/items", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "webflow_update_collection_item":
                cid = arguments["collection_id"]
                iid = arguments["item_id"]
                payload: dict[str, Any] = {"fieldData": arguments["fields"]}
                if "is_draft" in arguments:
                    payload["isDraft"] = arguments["is_draft"]
                r = await c.patch(f"/collections/{cid}/items/{iid}", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "webflow_publish_site":
                sid = _site_id(arguments)
                if not sid:
                    return {"error": "site_id required (or set WEBFLOW_SITE_ID)"}
                payload: dict[str, Any] = {}
                if domains := arguments.get("domains"):
                    payload["customDomains"] = [{"id": d} for d in domains]
                r = await c.post(f"/sites/{sid}/publish", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "webflow_get_site":
                sid = _site_id(arguments)
                if not sid:
                    return {"error": "site_id required (or set WEBFLOW_SITE_ID)"}
                r = await c.get(f"/sites/{sid}")
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("webflow_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
