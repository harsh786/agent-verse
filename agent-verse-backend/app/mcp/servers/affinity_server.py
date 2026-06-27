"""Affinity CRM MCP server — lists, list entries, persons, organizations.

Environment variables:
  AFFINITY_API_KEY: Affinity API key (used as HTTP Basic password; username is empty)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

AFFINITY_BASE = "https://api.affinity.co"

TOOL_DEFINITIONS = [
    {
        "name": "affinity_list_lists",
        "description": "List all Affinity lists (pipelines)",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "affinity_list_list_entries",
        "description": "List entries in an Affinity list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "integer"},
                "page_size": {"type": "integer", "default": 50},
                "page_token": {"type": "string", "description": "Pagination token"},
            },
            "required": ["list_id"],
        },
    },
    {
        "name": "affinity_create_list_entry",
        "description": "Add an entity to an Affinity list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "integer"},
                "entity_id": {"type": "integer", "description": "Person or Organization ID"},
            },
            "required": ["list_id", "entity_id"],
        },
    },
    {
        "name": "affinity_list_persons",
        "description": "List or search Affinity persons",
        "parameters": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "Search term"},
                "page_size": {"type": "integer", "default": 20},
                "page_token": {"type": "string"},
                "with_interaction_dates": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "affinity_list_organizations",
        "description": "List or search Affinity organizations",
        "parameters": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "Search term"},
                "page_size": {"type": "integer", "default": 20},
                "page_token": {"type": "string"},
                "with_interaction_dates": {"type": "boolean", "default": False},
            },
        },
    },
]


def _auth() -> tuple[str, str]:
    # Affinity uses HTTP Basic auth: empty username, API key as password
    return ("", os.getenv("AFFINITY_API_KEY", ""))


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("AFFINITY_API_KEY"):
        return {"error": "AFFINITY_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=AFFINITY_BASE, auth=_auth(), timeout=30.0
        ) as c:
            if tool_name == "affinity_list_lists":
                r = await c.get("/lists")
                r.raise_for_status()
                return r.json()

            elif tool_name == "affinity_list_list_entries":
                lid = arguments["list_id"]
                params: dict[str, Any] = {"page_size": arguments.get("page_size", 50)}
                if pt := arguments.get("page_token"):
                    params["page_token"] = pt
                r = await c.get(f"/lists/{lid}/list-entries", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "affinity_create_list_entry":
                lid = arguments["list_id"]
                r = await c.post(
                    f"/lists/{lid}/list-entries",
                    json={"entity_id": arguments["entity_id"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "affinity_list_persons":
                params = {"page_size": arguments.get("page_size", 20)}
                if term := arguments.get("term"):
                    params["term"] = term
                if pt := arguments.get("page_token"):
                    params["page_token"] = pt
                if arguments.get("with_interaction_dates"):
                    params["with_interaction_dates"] = "true"
                r = await c.get("/persons", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "affinity_list_organizations":
                params = {"page_size": arguments.get("page_size", 20)}
                if term := arguments.get("term"):
                    params["term"] = term
                if pt := arguments.get("page_token"):
                    params["page_token"] = pt
                if arguments.get("with_interaction_dates"):
                    params["with_interaction_dates"] = "true"
                r = await c.get("/organizations", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("affinity_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
