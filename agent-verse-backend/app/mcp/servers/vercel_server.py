"""Vercel MCP server — manage Vercel projects, deployments, and domains.

Environment variables:
  VERCEL_TOKEN:   Vercel personal access token or team token
  VERCEL_TEAM_ID: (optional) Team ID to scope requests (teamId=xxx)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

_API_BASE = "https://api.vercel.com"

TOOL_DEFINITIONS = [
    {
        "name": "vercel_list_projects",
        "description": "List all Vercel projects for the authenticated user or team",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "search": {"type": "string"},
            },
        },
    },
    {
        "name": "vercel_get_project",
        "description": "Get details of a specific Vercel project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project ID or name"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "vercel_list_deployments",
        "description": "List deployments, optionally filtered by project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
                "state": {
                    "type": "string",
                    "enum": ["BUILDING", "ERROR", "INITIALIZING", "QUEUED", "READY", "CANCELED"],
                },
            },
        },
    },
    {
        "name": "vercel_get_deployment",
        "description": "Get details of a specific Vercel deployment",
        "parameters": {
            "type": "object",
            "properties": {
                "deployment_id": {"type": "string"},
            },
            "required": ["deployment_id"],
        },
    },
    {
        "name": "vercel_create_deployment",
        "description": "Create a new Vercel deployment",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "git_source": {
                    "type": "object",
                    "description": "Git source (type, repo, ref)",
                    "properties": {
                        "type": {"type": "string"},
                        "repo": {"type": "string"},
                        "ref": {"type": "string"},
                    },
                },
                "target": {"type": "string", "enum": ["production", "staging"], "default": "staging"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "vercel_cancel_deployment",
        "description": "Cancel an in-progress Vercel deployment",
        "parameters": {
            "type": "object",
            "properties": {
                "deployment_id": {"type": "string"},
            },
            "required": ["deployment_id"],
        },
    },
    {
        "name": "vercel_list_domains",
        "description": "List all domains associated with the account or team",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "vercel_get_env_vars",
        "description": "Get environment variables for a Vercel project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("VERCEL_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _team_params(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    team_id = os.getenv("VERCEL_TEAM_ID", "")
    if team_id:
        params["teamId"] = team_id
    if extra:
        params.update(extra)
    return params


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=_API_BASE, headers=_headers(), timeout=30.0) as client:
        if tool_name == "vercel_list_projects":
            params = _team_params({"limit": arguments.get("limit", 20)})
            if arguments.get("search"):
                params["search"] = arguments["search"]
            resp = await client.get("/v9/projects", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "projects": [
                    {
                        "id": p["id"],
                        "name": p["name"],
                        "framework": p.get("framework"),
                        "link": p.get("link"),
                        "updated_at": p.get("updatedAt"),
                    }
                    for p in data.get("projects", [])
                ]
            }

        elif tool_name == "vercel_get_project":
            pid = arguments["project_id"]
            resp = await client.get(f"/v9/projects/{pid}", params=_team_params())
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data["id"],
                "name": data["name"],
                "framework": data.get("framework"),
                "git_repository": data.get("link"),
                "aliases": [a.get("domain") for a in data.get("alias", [])],
                "env": [e.get("key") for e in data.get("env", [])],
            }

        elif tool_name == "vercel_list_deployments":
            params = _team_params({"limit": arguments.get("limit", 20)})
            if arguments.get("project_id"):
                params["projectId"] = arguments["project_id"]
            if arguments.get("state"):
                params["state"] = arguments["state"]
            resp = await client.get("/v6/deployments", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "deployments": [
                    {
                        "uid": d["uid"],
                        "name": d.get("name"),
                        "state": d.get("state"),
                        "url": d.get("url"),
                        "created": d.get("created"),
                        "ready": d.get("ready"),
                        "target": d.get("target"),
                    }
                    for d in data.get("deployments", [])
                ]
            }

        elif tool_name == "vercel_get_deployment":
            did = arguments["deployment_id"]
            resp = await client.get(f"/v13/deployments/{did}", params=_team_params())
            resp.raise_for_status()
            data = resp.json()
            return {
                "uid": data["uid"],
                "name": data.get("name"),
                "state": data.get("state"),
                "url": data.get("url"),
                "created": data.get("createdAt"),
                "ready": data.get("readyAt"),
                "error": data.get("errorMessage"),
            }

        elif tool_name == "vercel_create_deployment":
            payload: dict[str, Any] = {
                "name": arguments["name"],
                "target": arguments.get("target", "staging"),
            }
            if arguments.get("git_source"):
                payload["gitSource"] = arguments["git_source"]
            resp = await client.post("/v13/deployments", json=payload, params=_team_params())
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data.get("id"),
                "uid": data.get("uid"),
                "state": data.get("state"),
                "url": data.get("url"),
                "ready_state": data.get("readyState"),
            }

        elif tool_name == "vercel_cancel_deployment":
            did = arguments["deployment_id"]
            resp = await client.patch(f"/v12/deployments/{did}/cancel", params=_team_params())
            resp.raise_for_status()
            data = resp.json()
            return {"uid": data.get("uid"), "state": data.get("state")}

        elif tool_name == "vercel_list_domains":
            params = _team_params({"limit": arguments.get("limit", 20)})
            resp = await client.get("/v5/domains", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "domains": [
                    {
                        "name": d.get("name"),
                        "verified": d.get("verified"),
                        "expiration_date": d.get("expirationDate"),
                        "service_type": d.get("serviceType"),
                    }
                    for d in data.get("domains", [])
                ]
            }

        elif tool_name == "vercel_get_env_vars":
            pid = arguments["project_id"]
            resp = await client.get(f"/v9/projects/{pid}/env", params=_team_params())
            resp.raise_for_status()
            data = resp.json()
            return {
                "env_vars": [
                    {
                        "id": e.get("id"),
                        "key": e.get("key"),
                        "target": e.get("target"),
                        "type": e.get("type"),
                        "git_branch": e.get("gitBranch"),
                    }
                    for e in data.get("envs", [])
                ]
            }

        else:
            return {"error": f"Unknown tool: {tool_name}"}
