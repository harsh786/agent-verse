"""Cloudflare MCP server — CDN, DNS, and Workers management via Cloudflare API v4.

Environment:
  CLOUDFLARE_API_TOKEN: Cloudflare API token with appropriate permissions
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

CF_BASE = "https://api.cloudflare.com/client/v4"

TOOL_DEFINITIONS = [
    {
        "name": "cf_list_zones",
        "description": "List all Cloudflare zones (domains) in the account",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Filter by zone name"},
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "cf_purge_cache",
        "description": "Purge cached files from Cloudflare edge for a zone",
        "parameters": {
            "type": "object",
            "properties": {
                "zone_id": {"type": "string"},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific URLs to purge; omit to purge everything",
                },
                "purge_everything": {
                    "type": "boolean",
                    "default": False,
                    "description": "Purge all cached content in the zone",
                },
            },
            "required": ["zone_id"],
        },
    },
    {
        "name": "cf_list_dns_records",
        "description": "List DNS records for a Cloudflare zone",
        "parameters": {
            "type": "object",
            "properties": {
                "zone_id": {"type": "string"},
                "type": {
                    "type": "string",
                    "description": "Record type filter e.g. A, CNAME, MX",
                },
                "name": {"type": "string"},
                "per_page": {"type": "integer", "default": 50},
            },
            "required": ["zone_id"],
        },
    },
    {
        "name": "cf_create_dns_record",
        "description": "Create a DNS record in a Cloudflare zone",
        "parameters": {
            "type": "object",
            "properties": {
                "zone_id": {"type": "string"},
                "type": {"type": "string", "description": "e.g. A, CNAME, MX, TXT"},
                "name": {"type": "string", "description": "DNS record name"},
                "content": {"type": "string", "description": "Record value"},
                "ttl": {"type": "integer", "default": 1, "description": "1 = auto"},
                "proxied": {"type": "boolean", "default": False},
            },
            "required": ["zone_id", "type", "name", "content"],
        },
    },
    {
        "name": "cf_list_workers",
        "description": "List Cloudflare Workers scripts in the account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Cloudflare account ID"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "cf_deploy_worker",
        "description": "Deploy or update a Cloudflare Worker script",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "script_name": {"type": "string", "description": "Worker script identifier"},
                "script_content": {"type": "string", "description": "JavaScript worker code"},
                "compatibility_date": {
                    "type": "string",
                    "description": "e.g. 2024-01-01",
                },
            },
            "required": ["account_id", "script_name", "script_content"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("CLOUDFLARE_API_TOKEN", "")
    if not token:
        return {"error": "CLOUDFLARE_API_TOKEN not configured"}

    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "cf_list_zones":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 20),
                }
                if arguments.get("name"):
                    params["name"] = arguments["name"]
                r = await c.get(f"{CF_BASE}/zones", headers=hdrs, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "zones": [
                        {
                            "id": z.get("id"),
                            "name": z.get("name"),
                            "status": z.get("status"),
                            "plan": z.get("plan", {}).get("name"),
                        }
                        for z in data.get("result", [])
                    ]
                }

            elif tool_name == "cf_purge_cache":
                zone_id = arguments["zone_id"]
                body: dict[str, Any] = {}
                if arguments.get("purge_everything"):
                    body["purge_everything"] = True
                elif arguments.get("files"):
                    body["files"] = arguments["files"]
                else:
                    body["purge_everything"] = True
                r = await c.post(
                    f"{CF_BASE}/zones/{zone_id}/purge_cache",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return {"purged": True, "zone_id": zone_id}

            elif tool_name == "cf_list_dns_records":
                zone_id = arguments["zone_id"]
                params = {"per_page": arguments.get("per_page", 50)}
                if arguments.get("type"):
                    params["type"] = arguments["type"]
                if arguments.get("name"):
                    params["name"] = arguments["name"]
                r = await c.get(
                    f"{CF_BASE}/zones/{zone_id}/dns_records",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "records": [
                        {
                            "id": rec.get("id"),
                            "type": rec.get("type"),
                            "name": rec.get("name"),
                            "content": rec.get("content"),
                            "proxied": rec.get("proxied"),
                            "ttl": rec.get("ttl"),
                        }
                        for rec in data.get("result", [])
                    ]
                }

            elif tool_name == "cf_create_dns_record":
                zone_id = arguments["zone_id"]
                body = {
                    "type": arguments["type"],
                    "name": arguments["name"],
                    "content": arguments["content"],
                    "ttl": arguments.get("ttl", 1),
                    "proxied": arguments.get("proxied", False),
                }
                r = await c.post(
                    f"{CF_BASE}/zones/{zone_id}/dns_records",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                result = r.json().get("result", {})
                return {
                    "id": result.get("id"),
                    "name": result.get("name"),
                    "type": result.get("type"),
                    "created": True,
                }

            elif tool_name == "cf_list_workers":
                account_id = arguments["account_id"]
                r = await c.get(
                    f"{CF_BASE}/accounts/{account_id}/workers/scripts",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "workers": [
                        {"id": w.get("id"), "etag": w.get("etag")}
                        for w in data.get("result", [])
                    ]
                }

            elif tool_name == "cf_deploy_worker":
                account_id = arguments["account_id"]
                script_name = arguments["script_name"]
                r = await c.put(
                    f"{CF_BASE}/accounts/{account_id}/workers/scripts/{script_name}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/javascript",
                    },
                    content=arguments["script_content"].encode(),
                )
                r.raise_for_status()
                return {"deployed": True, "script_name": script_name}

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("cf_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
