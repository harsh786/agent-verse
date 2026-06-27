"""Attio MCP server — records, notes on a modern CRM platform.

Environment variables:
  ATTIO_API_KEY: Attio API key (Bearer token)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

ATTIO_BASE = "https://api.attio.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "attio_list_records",
        "description": "List records for a given Attio object (e.g. people, companies, deals)",
        "parameters": {
            "type": "object",
            "properties": {
                "object_slug": {
                    "type": "string",
                    "description": "Object slug, e.g. people, companies, deals",
                },
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
                "sort_by": {"type": "string"},
                "sort_direction": {"type": "string", "enum": ["asc", "desc"]},
            },
            "required": ["object_slug"],
        },
    },
    {
        "name": "attio_create_record",
        "description": "Create a new record in an Attio object",
        "parameters": {
            "type": "object",
            "properties": {
                "object_slug": {"type": "string"},
                "attributes": {
                    "type": "object",
                    "description": "Attribute slug-value pairs for the new record",
                },
            },
            "required": ["object_slug", "attributes"],
        },
    },
    {
        "name": "attio_update_record",
        "description": "Update attributes of an existing Attio record",
        "parameters": {
            "type": "object",
            "properties": {
                "object_slug": {"type": "string"},
                "record_id": {"type": "string"},
                "attributes": {"type": "object"},
            },
            "required": ["object_slug", "record_id", "attributes"],
        },
    },
    {
        "name": "attio_list_notes",
        "description": "List notes for a specific Attio record",
        "parameters": {
            "type": "object",
            "properties": {
                "object_slug": {"type": "string"},
                "record_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["object_slug", "record_id"],
        },
    },
    {
        "name": "attio_create_note",
        "description": "Create a note on an Attio record",
        "parameters": {
            "type": "object",
            "properties": {
                "object_slug": {"type": "string"},
                "record_id": {"type": "string"},
                "title": {"type": "string"},
                "content_plaintext": {"type": "string", "description": "Note body as plain text"},
            },
            "required": ["object_slug", "record_id", "title", "content_plaintext"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("ATTIO_API_KEY", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("ATTIO_API_KEY", "")
    if not token:
        return {"error": "ATTIO_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=ATTIO_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "attio_list_records":
                slug = arguments["object_slug"]
                body: dict[str, Any] = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if sb := arguments.get("sort_by"):
                    body["sorts"] = [
                        {"attribute": sb, "direction": arguments.get("sort_direction", "asc")}
                    ]
                r = await c.post(f"/objects/{slug}/records/query", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "attio_create_record":
                slug = arguments["object_slug"]
                r = await c.post(
                    f"/objects/{slug}/records",
                    json={"data": {"values": arguments["attributes"]}},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "attio_update_record":
                slug, rid = arguments["object_slug"], arguments["record_id"]
                r = await c.patch(
                    f"/objects/{slug}/records/{rid}",
                    json={"data": {"values": arguments["attributes"]}},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "attio_list_notes":
                slug, rid = arguments["object_slug"], arguments["record_id"]
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                    "parent_object": slug,
                    "parent_record_id": rid,
                }
                r = await c.get("/notes", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "attio_create_note":
                slug, rid = arguments["object_slug"], arguments["record_id"]
                payload = {
                    "data": {
                        "parent_object": slug,
                        "parent_record_id": rid,
                        "title": arguments["title"],
                        "content_plaintext": arguments["content_plaintext"],
                    }
                }
                r = await c.post("/notes", json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("attio_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
