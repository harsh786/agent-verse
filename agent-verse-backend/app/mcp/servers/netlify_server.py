"""Netlify MCP server — manage Netlify sites, deploys, and functions.

Environment variables:
  NETLIFY_ACCESS_TOKEN: Netlify personal access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

_API_BASE = "https://api.netlify.com/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "netlify_list_sites",
        "description": "List all Netlify sites for the authenticated account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 20},
                "filter": {"type": "string", "enum": ["all", "owner", "guest"], "default": "all"},
            },
        },
    },
    {
        "name": "netlify_get_site",
        "description": "Get details of a specific Netlify site",
        "parameters": {
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
            },
            "required": ["site_id"],
        },
    },
    {
        "name": "netlify_list_deploys",
        "description": "List deploys for a Netlify site",
        "parameters": {
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 20},
            },
            "required": ["site_id"],
        },
    },
    {
        "name": "netlify_get_deploy",
        "description": "Get details of a specific Netlify deploy",
        "parameters": {
            "type": "object",
            "properties": {
                "deploy_id": {"type": "string"},
            },
            "required": ["deploy_id"],
        },
    },
    {
        "name": "netlify_create_deploy",
        "description": "Create a new deploy for a Netlify site (triggers rebuild)",
        "parameters": {
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
                "title": {"type": "string", "description": "Optional deploy title"},
                "clear_cache": {"type": "boolean", "default": False},
            },
            "required": ["site_id"],
        },
    },
    {
        "name": "netlify_lock_deploy",
        "description": "Lock a Netlify deploy to stop auto-publishing",
        "parameters": {
            "type": "object",
            "properties": {
                "deploy_id": {"type": "string"},
            },
            "required": ["deploy_id"],
        },
    },
    {
        "name": "netlify_publish_deploy",
        "description": "Publish (restore/activate) a specific Netlify deploy",
        "parameters": {
            "type": "object",
            "properties": {
                "deploy_id": {"type": "string"},
            },
            "required": ["deploy_id"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("NETLIFY_ACCESS_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=_API_BASE, headers=_headers(), timeout=30.0) as client:
        if tool_name == "netlify_list_sites":
            params: dict[str, Any] = {
                "page": arguments.get("page", 1),
                "per_page": arguments.get("per_page", 20),
                "filter": arguments.get("filter", "all"),
            }
            resp = await client.get("/sites", params=params)
            resp.raise_for_status()
            return {
                "sites": [
                    {
                        "id": s["id"],
                        "name": s.get("name"),
                        "url": s.get("url"),
                        "ssl_url": s.get("ssl_url"),
                        "state": s.get("state"),
                        "published_deploy": {
                            "id": s.get("published_deploy", {}).get("id"),
                            "state": s.get("published_deploy", {}).get("state"),
                        } if s.get("published_deploy") else None,
                    }
                    for s in resp.json()
                ]
            }

        elif tool_name == "netlify_get_site":
            site_id = arguments["site_id"]
            resp = await client.get(f"/sites/{site_id}")
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data["id"],
                "name": data.get("name"),
                "url": data.get("url"),
                "ssl_url": data.get("ssl_url"),
                "state": data.get("state"),
                "repo_url": data.get("build_settings", {}).get("repo_url"),
                "branch": data.get("build_settings", {}).get("branch"),
                "publish_dir": data.get("build_settings", {}).get("dir"),
                "build_command": data.get("build_settings", {}).get("cmd"),
            }

        elif tool_name == "netlify_list_deploys":
            site_id = arguments["site_id"]
            params = {
                "page": arguments.get("page", 1),
                "per_page": arguments.get("per_page", 20),
            }
            resp = await client.get(f"/sites/{site_id}/deploys", params=params)
            resp.raise_for_status()
            return {
                "deploys": [
                    {
                        "id": d["id"],
                        "state": d.get("state"),
                        "branch": d.get("branch"),
                        "commit_ref": d.get("commit_ref"),
                        "title": d.get("title"),
                        "created_at": d.get("created_at"),
                        "published_at": d.get("published_at"),
                        "deploy_ssl_url": d.get("deploy_ssl_url"),
                    }
                    for d in resp.json()
                ]
            }

        elif tool_name == "netlify_get_deploy":
            deploy_id = arguments["deploy_id"]
            resp = await client.get(f"/deploys/{deploy_id}")
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data["id"],
                "state": data.get("state"),
                "branch": data.get("branch"),
                "commit_ref": data.get("commit_ref"),
                "error_message": data.get("error_message"),
                "created_at": data.get("created_at"),
                "published_at": data.get("published_at"),
                "deploy_ssl_url": data.get("deploy_ssl_url"),
            }

        elif tool_name == "netlify_create_deploy":
            site_id = arguments["site_id"]
            payload: dict[str, Any] = {}
            if arguments.get("title"):
                payload["title"] = arguments["title"]
            if arguments.get("clear_cache"):
                payload["clear_cache"] = True
            resp = await client.post(f"/sites/{site_id}/deploys", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data["id"],
                "state": data.get("state"),
                "deploy_ssl_url": data.get("deploy_ssl_url"),
                "created_at": data.get("created_at"),
            }

        elif tool_name == "netlify_lock_deploy":
            deploy_id = arguments["deploy_id"]
            resp = await client.post(f"/deploys/{deploy_id}/lock")
            resp.raise_for_status()
            data = resp.json()
            return {"id": data.get("id"), "locked": data.get("locked", True)}

        elif tool_name == "netlify_publish_deploy":
            deploy_id = arguments["deploy_id"]
            resp = await client.post(f"/deploys/{deploy_id}/restore")
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data.get("id"),
                "state": data.get("state"),
                "published_at": data.get("published_at"),
            }

        else:
            return {"error": f"Unknown tool: {tool_name}"}
