"""Bitly MCP server — URL shortening and click analytics.

Environment:
  BITLY_ACCESS_TOKEN: Bitly OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BITLY_BASE = "https://api-ssl.bitly.com/v4"

TOOL_DEFINITIONS = [
    {
        "name": "bitly_shorten_url",
        "description": "Shorten a long URL using Bitly",
        "parameters": {
            "type": "object",
            "properties": {
                "long_url": {"type": "string", "description": "The URL to shorten"},
                "domain": {"type": "string", "default": "bit.ly"},
                "group_guid": {"type": "string", "description": "Bitly group GUID"},
                "title": {"type": "string"},
            },
            "required": ["long_url"],
        },
    },
    {
        "name": "bitly_expand_url",
        "description": "Expand a Bitly short link to the original URL",
        "parameters": {
            "type": "object",
            "properties": {
                "bitlink_id": {"type": "string", "description": "Bitlink (e.g. bit.ly/abc123)"},
            },
            "required": ["bitlink_id"],
        },
    },
    {
        "name": "bitly_get_click_metrics",
        "description": "Get click metrics for a Bitly link",
        "parameters": {
            "type": "object",
            "properties": {
                "bitlink_id": {"type": "string"},
                "unit": {
                    "type": "string",
                    "enum": ["minute", "hour", "day", "week", "month"],
                    "default": "day",
                },
                "units": {"type": "integer", "default": 30},
            },
            "required": ["bitlink_id"],
        },
    },
    {
        "name": "bitly_list_bitlinks",
        "description": "List Bitlinks (short links) in a group",
        "parameters": {
            "type": "object",
            "properties": {
                "group_guid": {"type": "string"},
                "size": {"type": "integer", "default": 50},
                "keyword": {"type": "string", "description": "Search filter"},
            },
            "required": ["group_guid"],
        },
    },
    {
        "name": "bitly_create_group",
        "description": "Create a new Bitly group",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "organization_guid": {"type": "string"},
            },
            "required": ["name", "organization_guid"],
        },
    },
    {
        "name": "bitly_get_bitlink_info",
        "description": "Get metadata and details for a specific Bitlink",
        "parameters": {
            "type": "object",
            "properties": {
                "bitlink_id": {"type": "string"},
            },
            "required": ["bitlink_id"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("BITLY_ACCESS_TOKEN", "")
    if not token:
        return {"error": "BITLY_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "bitly_shorten_url":
                body: dict[str, Any] = {
                    "long_url": arguments["long_url"],
                    "domain": arguments.get("domain", "bit.ly"),
                }
                if arguments.get("group_guid"):
                    body["group_guid"] = arguments["group_guid"]
                if arguments.get("title"):
                    body["title"] = arguments["title"]
                r = await c.post(f"{BITLY_BASE}/shorten", headers=hdrs, json=body)
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "link": data.get("link"),
                    "long_url": data.get("long_url"),
                    "created_at": data.get("created_at"),
                }

            elif tool_name == "bitly_expand_url":
                bitlink_id = arguments["bitlink_id"].lstrip("https://").lstrip("http://")
                r = await c.post(
                    f"{BITLY_BASE}/expand",
                    headers=hdrs,
                    json={"bitlink_id": bitlink_id},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "long_url": data.get("long_url"),
                    "created_at": data.get("created_at"),
                }

            elif tool_name == "bitly_get_click_metrics":
                bitlink_id = arguments["bitlink_id"].lstrip("https://").lstrip("http://")
                r = await c.get(
                    f"{BITLY_BASE}/bitlinks/{bitlink_id}/clicks",
                    headers=hdrs,
                    params={
                        "unit": arguments.get("unit", "day"),
                        "units": arguments.get("units", 30),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "link_clicks": data.get("link_clicks", []),
                    "unit": data.get("unit"),
                    "units": data.get("units"),
                }

            elif tool_name == "bitly_list_bitlinks":
                group_guid = arguments["group_guid"]
                params: dict[str, Any] = {"size": arguments.get("size", 50)}
                if arguments.get("keyword"):
                    params["keyword"] = arguments["keyword"]
                r = await c.get(
                    f"{BITLY_BASE}/groups/{group_guid}/bitlinks",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "links": [
                        {
                            "id": lnk.get("id"),
                            "link": lnk.get("link"),
                            "long_url": lnk.get("long_url"),
                            "title": lnk.get("title"),
                        }
                        for lnk in data.get("links", [])
                    ],
                    "total": data.get("total", 0),
                }

            elif tool_name == "bitly_create_group":
                r = await c.post(
                    f"{BITLY_BASE}/groups",
                    headers=hdrs,
                    json={
                        "name": arguments["name"],
                        "organization_guid": arguments["organization_guid"],
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "guid": data.get("guid"),
                    "name": data.get("name"),
                    "created": True,
                }

            elif tool_name == "bitly_get_bitlink_info":
                bitlink_id = arguments["bitlink_id"].lstrip("https://").lstrip("http://")
                r = await c.get(
                    f"{BITLY_BASE}/bitlinks/{bitlink_id}",
                    headers=hdrs,
                )
                r.raise_for_status()
                return r.json()

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("bitly_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
