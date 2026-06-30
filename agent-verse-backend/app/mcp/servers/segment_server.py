"""Segment MCP server — customer data platform: identify, track, page, group, and alias.

Environment variables:
  SEGMENT_WRITE_KEY: Segment Source write key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

SEGMENT_BASE = "https://api.segment.io/v1"

TOOL_DEFINITIONS = [
    {
        "name": "segment_identify_user",
        "description": "Identify a user in Segment and set traits like name, email, plan, and custom attributes",
        "parameters": {
            "type": "object",
            "properties": {
                "userId": {"type": "string", "description": "Your database user ID"},
                "anonymousId": {"type": "string", "description": "Anonymous ID (use if no userId yet)"},
                "traits": {
                    "type": "object",
                    "description": "User traits, e.g. {name, email, plan, company, createdAt}",
                },
                "context": {"type": "object", "description": "Segment context object"},
            },
        },
    },
    {
        "name": "segment_track_event",
        "description": "Track a user action or event in Segment to trigger downstream integrations",
        "parameters": {
            "type": "object",
            "properties": {
                "userId": {"type": "string"},
                "anonymousId": {"type": "string"},
                "event": {"type": "string", "description": "Event name, e.g. 'Order Completed', 'Button Clicked'"},
                "properties": {
                    "type": "object",
                    "description": "Event properties, e.g. {revenue, currency, productName}",
                },
                "context": {"type": "object"},
            },
            "required": ["event"],
        },
    },
    {
        "name": "segment_page_view",
        "description": "Record a page view event in Segment",
        "parameters": {
            "type": "object",
            "properties": {
                "userId": {"type": "string"},
                "anonymousId": {"type": "string"},
                "name": {"type": "string", "description": "Page name, e.g. 'Home', 'Pricing'"},
                "properties": {
                    "type": "object",
                    "description": "Page properties, e.g. {url, title, referrer}",
                },
                "context": {"type": "object"},
            },
        },
    },
    {
        "name": "segment_group_user",
        "description": "Associate a user with a group (company/account) in Segment",
        "parameters": {
            "type": "object",
            "properties": {
                "userId": {"type": "string", "description": "User ID to associate"},
                "groupId": {"type": "string", "description": "Group/company ID"},
                "traits": {
                    "type": "object",
                    "description": "Group traits, e.g. {name, industry, employees, plan}",
                },
            },
            "required": ["groupId"],
        },
    },
    {
        "name": "segment_alias_user",
        "description": "Alias an anonymous ID to a known user ID in Segment",
        "parameters": {
            "type": "object",
            "properties": {
                "userId": {"type": "string", "description": "New canonical user ID"},
                "previousId": {"type": "string", "description": "Previous anonymous or user ID to alias"},
            },
            "required": ["userId", "previousId"],
        },
    },
    {
        "name": "segment_list_sources",
        "description": "List Segment sources (data inputs) configured in your workspace via the Config API",
        "parameters": {
            "type": "object",
            "properties": {
                "pagination_cursor": {"type": "string", "description": "Pagination cursor for next page"},
            },
        },
    },
]


def _auth(write_key: str) -> tuple[str, str]:
    return (write_key, "")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    write_key = os.getenv("SEGMENT_WRITE_KEY", "")
    if not write_key:
        return {"error": "SEGMENT_WRITE_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=SEGMENT_BASE,
            auth=_auth(write_key),
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        ) as c:
            if tool_name == "segment_identify_user":
                body: dict[str, Any] = {}
                if "userId" in arguments:
                    body["userId"] = arguments["userId"]
                if "anonymousId" in arguments:
                    body["anonymousId"] = arguments["anonymousId"]
                if "traits" in arguments:
                    body["traits"] = arguments["traits"]
                if "context" in arguments:
                    body["context"] = arguments["context"]
                r = await c.post("/identify", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "segment_track_event":
                body = {"event": arguments["event"]}
                for k in ("userId", "anonymousId", "properties", "context"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/track", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "segment_page_view":
                body = {}
                for k in ("userId", "anonymousId", "name", "properties", "context"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/page", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "segment_group_user":
                body = {"groupId": arguments["groupId"]}
                for k in ("userId", "traits"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/group", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "segment_alias_user":
                body = {
                    "userId": arguments["userId"],
                    "previousId": arguments["previousId"],
                }
                r = await c.post("/alias", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "segment_list_sources":
                # Uses the Segment Public API (not the tracking API)
                segment_api_url = "https://api.segmentapis.com/sources"
                params: dict[str, Any] = {}
                if "pagination_cursor" in arguments:
                    params["pagination[cursor]"] = arguments["pagination_cursor"]
                r = await c.get(segment_api_url, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("segment_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
