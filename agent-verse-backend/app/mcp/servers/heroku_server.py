"""Heroku MCP server — manage Heroku apps, dynos, and pipelines via Platform API.

Environment variables:
  HEROKU_API_KEY: Heroku API key (available from Account Settings)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

_API_BASE = "https://api.heroku.com"

TOOL_DEFINITIONS = [
    {
        "name": "heroku_list_apps",
        "description": "List all Heroku apps for the authenticated account",
        "parameters": {
            "type": "object",
            "properties": {
                "team": {"type": "string", "description": "Filter by team/organization name"},
            },
        },
    },
    {
        "name": "heroku_get_app",
        "description": "Get details of a specific Heroku app",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "App name or ID"},
            },
            "required": ["app"],
        },
    },
    {
        "name": "heroku_list_dynos",
        "description": "List dynos (processes) for a Heroku app",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
            },
            "required": ["app"],
        },
    },
    {
        "name": "heroku_restart_dyno",
        "description": "Restart all dynos or a specific dyno for a Heroku app",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
                "dyno": {"type": "string", "description": "Dyno name (e.g. web.1); omit to restart all"},
            },
            "required": ["app"],
        },
    },
    {
        "name": "heroku_list_releases",
        "description": "List recent releases for a Heroku app",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
            },
            "required": ["app"],
        },
    },
    {
        "name": "heroku_rollback",
        "description": "Rollback a Heroku app to a previous release",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
                "release": {"type": "string", "description": "Release ID or version number"},
            },
            "required": ["app", "release"],
        },
    },
    {
        "name": "heroku_list_config_vars",
        "description": "List all config vars (environment variables) for a Heroku app",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
            },
            "required": ["app"],
        },
    },
    {
        "name": "heroku_set_config_var",
        "description": "Set or update config vars for a Heroku app",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
                "vars": {
                    "type": "object",
                    "description": "Key-value config vars to set",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["app", "vars"],
        },
    },
    {
        "name": "heroku_list_addons",
        "description": "List add-ons provisioned for a Heroku app",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
            },
            "required": ["app"],
        },
    },
]


def _headers() -> dict[str, str]:
    api_key = os.getenv("HEROKU_API_KEY", "")
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/vnd.heroku+json; version=3",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        return await _call_tool_inner(tool_name, arguments)
    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.text[:500]
        except Exception:
            pass
        return {"error": f"HTTP {exc.response.status_code}: {error_body or exc.response.reason_phrase}", "status_code": exc.response.status_code}
    except Exception as exc:
        logger.error("call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}


async def _call_tool_inner(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=_API_BASE, headers=_headers(), timeout=30.0) as client:
        if tool_name == "heroku_list_apps":
            if arguments.get("team"):
                resp = await client.get(f"/teams/{arguments['team']}/apps")
            else:
                resp = await client.get("/apps")
            resp.raise_for_status()
            return {
                "apps": [
                    {
                        "id": a["id"],
                        "name": a["name"],
                        "region": a.get("region", {}).get("name"),
                        "stack": a.get("stack", {}).get("name"),
                        "web_url": a.get("web_url"),
                        "updated_at": a.get("updated_at"),
                    }
                    for a in resp.json()
                ]
            }

        elif tool_name == "heroku_get_app":
            app = arguments["app"]
            resp = await client.get(f"/apps/{app}")
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data["id"],
                "name": data["name"],
                "region": data.get("region", {}).get("name"),
                "stack": data.get("stack", {}).get("name"),
                "web_url": data.get("web_url"),
                "git_url": data.get("git_url"),
                "owner": data.get("owner", {}).get("email"),
                "created_at": data.get("created_at"),
            }

        elif tool_name == "heroku_list_dynos":
            app = arguments["app"]
            resp = await client.get(f"/apps/{app}/dynos")
            resp.raise_for_status()
            return {
                "dynos": [
                    {
                        "id": d["id"],
                        "name": d.get("name"),
                        "type": d.get("type"),
                        "state": d.get("state"),
                        "size": d.get("size"),
                        "updated_at": d.get("updated_at"),
                    }
                    for d in resp.json()
                ]
            }

        elif tool_name == "heroku_restart_dyno":
            app = arguments["app"]
            if arguments.get("dyno"):
                resp = await client.delete(f"/apps/{app}/dynos/{arguments['dyno']}")
            else:
                resp = await client.delete(f"/apps/{app}/dynos")
            if resp.status_code in (200, 202, 204):
                return {"restarted": True, "app": app}
            resp.raise_for_status()
            return {"restarted": True}

        elif tool_name == "heroku_list_releases":
            app = arguments["app"]
            resp = await client.get(
                f"/apps/{app}/releases",
                headers={**_headers(), "Range": "version ..; max=20, order=desc"},
            )
            resp.raise_for_status()
            return {
                "releases": [
                    {
                        "id": r["id"],
                        "version": r.get("version"),
                        "description": r.get("description"),
                        "status": r.get("status"),
                        "created_at": r.get("created_at"),
                        "user": r.get("user", {}).get("email"),
                    }
                    for r in resp.json()
                ]
            }

        elif tool_name == "heroku_rollback":
            app = arguments["app"]
            payload = {"release": arguments["release"]}
            resp = await client.post(f"/apps/{app}/releases", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data.get("id"),
                "version": data.get("version"),
                "status": data.get("status"),
            }

        elif tool_name == "heroku_list_config_vars":
            app = arguments["app"]
            resp = await client.get(f"/apps/{app}/config-vars")
            resp.raise_for_status()
            data = resp.json()
            return {"config_vars": {k: "***" if "KEY" in k.upper() or "SECRET" in k.upper() or "PASSWORD" in k.upper() else v for k, v in data.items()}}

        elif tool_name == "heroku_set_config_var":
            app = arguments["app"]
            resp = await client.patch(f"/apps/{app}/config-vars", json=arguments["vars"])
            resp.raise_for_status()
            return {"updated": True, "keys": list(arguments["vars"].keys())}

        elif tool_name == "heroku_list_addons":
            app = arguments["app"]
            resp = await client.get(f"/apps/{app}/addons")
            resp.raise_for_status()
            return {
                "addons": [
                    {
                        "id": a["id"],
                        "name": a.get("name"),
                        "plan": a.get("plan", {}).get("name"),
                        "state": a.get("state"),
                        "app": a.get("app", {}).get("name"),
                    }
                    for a in resp.json()
                ]
            }

        else:
            return {"error": f"Unknown tool: {tool_name}"}
