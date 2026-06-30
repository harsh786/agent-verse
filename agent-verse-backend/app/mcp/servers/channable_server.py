"""Channable MCP server — product feed management, channel publishing, and rules.

Environment:
  CHANNABLE_API_KEY: Channable API key for authentication
  CHANNABLE_COMPANY_ID: Channable company/account ID
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://app.channable.com/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "channable_list_feeds",
        "description": "List all channel feeds configured in a Channable project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "Channable project ID"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "channable_get_feed_status",
        "description": "Get the current processing status and last update for a feed",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "Project ID"},
                "feed_id": {"type": "integer", "description": "Feed ID"},
            },
            "required": ["project_id", "feed_id"],
        },
    },
    {
        "name": "channable_list_projects",
        "description": "List all Channable projects in the account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
        },
    },
    {
        "name": "channable_export_feed",
        "description": "Trigger a manual export of a product feed",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "Project ID"},
                "feed_id": {"type": "integer", "description": "Feed ID to export"},
            },
            "required": ["project_id", "feed_id"],
        },
    },
    {
        "name": "channable_list_rules",
        "description": "List data transformation rules for a Channable project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "Project ID"},
                "page": {"type": "integer", "description": "Page number"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "channable_get_performance",
        "description": "Get performance metrics for feeds in a Channable project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "Project ID"},
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["project_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("CHANNABLE_API_KEY", "")
    company_id = os.getenv("CHANNABLE_COMPANY_ID", "")
    if not api_key:
        return {"error": "CHANNABLE_API_KEY not configured"}
    if not company_id:
        return {"error": "CHANNABLE_COMPANY_ID not configured"}

    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "channable_list_feeds":
                project_id = arguments["project_id"]
                r = await client.get(
                    f"{BASE_URL}/companies/{company_id}/projects/{project_id}/feeds",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "channable_get_feed_status":
                r = await client.get(
                    f"{BASE_URL}/companies/{company_id}/projects/{arguments['project_id']}/feeds/{arguments['feed_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "channable_list_projects":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(
                    f"{BASE_URL}/companies/{company_id}/projects",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "channable_export_feed":
                r = await client.post(
                    f"{BASE_URL}/companies/{company_id}/projects/{arguments['project_id']}/feeds/{arguments['feed_id']}/export",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "channable_list_rules":
                params = {}
                if "page" in arguments:
                    params["page"] = arguments["page"]
                r = await client.get(
                    f"{BASE_URL}/companies/{company_id}/projects/{arguments['project_id']}/rules",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "channable_get_performance":
                params: dict[str, Any] = {}
                if "start_date" in arguments:
                    params["start_date"] = arguments["start_date"]
                if "end_date" in arguments:
                    params["end_date"] = arguments["end_date"]
                r = await client.get(
                    f"{BASE_URL}/companies/{company_id}/projects/{arguments['project_id']}/statistics",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
