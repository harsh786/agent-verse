"""Kajabi MCP server — Kajabi online courses, members, offers, and pipeline management.

Environment:
  KAJABI_API_KEY: Kajabi API key from Settings > API
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://kajabi.com/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "kajabi_list_products",
        "description": "List all Kajabi products (courses, podcasts, coaching programs, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number for pagination", "default": 1},
            },
        },
    },
    {
        "name": "kajabi_list_members",
        "description": "List members of the Kajabi site",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Filter members by email address"},
                "page": {"type": "integer", "description": "Page number", "default": 1},
            },
        },
    },
    {
        "name": "kajabi_get_member",
        "description": "Get details of a specific Kajabi member by their ID",
        "parameters": {
            "type": "object",
            "properties": {
                "member_id": {"type": "string", "description": "Kajabi member ID"},
            },
            "required": ["member_id"],
        },
    },
    {
        "name": "kajabi_update_member_grant",
        "description": "Grant or revoke access to a Kajabi product for a member",
        "parameters": {
            "type": "object",
            "properties": {
                "member_id": {"type": "string", "description": "Kajabi member ID"},
                "product_id": {"type": "string", "description": "Kajabi product ID"},
                "action": {"type": "string", "description": "Action: grant or revoke", "default": "grant"},
            },
            "required": ["member_id", "product_id"],
        },
    },
    {
        "name": "kajabi_list_offers",
        "description": "List all Kajabi offers (pricing plans) for products",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Filter offers by product ID"},
                "page": {"type": "integer", "description": "Page number", "default": 1},
            },
        },
    },
    {
        "name": "kajabi_list_pipelines",
        "description": "List marketing pipelines (funnels) in the Kajabi site",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number for pagination", "default": 1},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("KAJABI_API_KEY", "")
    if not api_key:
        return {"error": "KAJABI_API_KEY not configured"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "kajabi_list_products":
                r = await client.get(
                    f"{BASE}/products",
                    headers=headers,
                    params={"page": arguments.get("page", 1)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "products": [
                        {
                            "id": p.get("id"),
                            "title": p.get("title"),
                            "type": p.get("type"),
                            "status": p.get("status"),
                        }
                        for p in data.get("data", data if isinstance(data, list) else [])
                    ],
                }

            elif tool_name == "kajabi_list_members":
                params: dict[str, Any] = {"page": arguments.get("page", 1)}
                if "email" in arguments:
                    params["email"] = arguments["email"]
                r = await client.get(f"{BASE}/members", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                members = data.get("data", data if isinstance(data, list) else [])
                return {
                    "members": [
                        {
                            "id": m.get("id"),
                            "email": m.get("email"),
                            "full_name": m.get("full_name"),
                            "joined_at": m.get("created_at"),
                        }
                        for m in members
                    ],
                    "total": data.get("total", len(members)) if isinstance(data, dict) else len(members),
                }

            elif tool_name == "kajabi_get_member":
                r = await client.get(
                    f"{BASE}/members/{arguments['member_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "kajabi_update_member_grant":
                action = arguments.get("action", "grant")
                payload: dict[str, Any] = {
                    "member_id": arguments["member_id"],
                    "product_id": arguments["product_id"],
                }
                endpoint = f"{BASE}/grants"
                if action == "revoke":
                    r = await client.delete(endpoint, headers=headers, json=payload)
                else:
                    r = await client.post(endpoint, headers=headers, json=payload)
                r.raise_for_status()
                return {"action": action, "member_id": arguments["member_id"], "product_id": arguments["product_id"]}

            elif tool_name == "kajabi_list_offers":
                params = {"page": arguments.get("page", 1)}
                if "product_id" in arguments:
                    params["product_id"] = arguments["product_id"]
                r = await client.get(f"{BASE}/offers", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {"offers": data.get("data", data if isinstance(data, list) else [])}

            elif tool_name == "kajabi_list_pipelines":
                r = await client.get(
                    f"{BASE}/pipelines",
                    headers=headers,
                    params={"page": arguments.get("page", 1)},
                )
                r.raise_for_status()
                data = r.json()
                return {"pipelines": data.get("data", data if isinstance(data, list) else [])}

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("kajabi_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
