"""Bitbucket MCP server — wraps Bitbucket Cloud REST API 2.0.

Environment variables:
  BITBUCKET_USERNAME:     Bitbucket account username
  BITBUCKET_APP_PASSWORD: App password (not your login password)
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

_API_BASE = "https://api.bitbucket.org/2.0"

TOOL_DEFINITIONS = [
    {
        "name": "bitbucket_list_repos",
        "description": "List repositories in a Bitbucket workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Workspace slug or UUID"},
                "page": {"type": "integer", "default": 1},
                "pagelen": {"type": "integer", "default": 20},
            },
            "required": ["workspace"],
        },
    },
    {
        "name": "bitbucket_list_issues",
        "description": "List issues in a Bitbucket repository",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "repo_slug": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["new", "open", "resolved", "on hold", "invalid", "wontfix", "closed"],
                },
                "pagelen": {"type": "integer", "default": 20},
            },
            "required": ["workspace", "repo_slug"],
        },
    },
    {
        "name": "bitbucket_create_issue",
        "description": "Create an issue in a Bitbucket repository",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "repo_slug": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string", "default": ""},
                "kind": {
                    "type": "string",
                    "enum": ["bug", "enhancement", "proposal", "task"],
                    "default": "bug",
                },
                "priority": {
                    "type": "string",
                    "enum": ["trivial", "minor", "major", "critical", "blocker"],
                    "default": "major",
                },
            },
            "required": ["workspace", "repo_slug", "title"],
        },
    },
    {
        "name": "bitbucket_list_pull_requests",
        "description": "List pull requests in a Bitbucket repository",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "repo_slug": {"type": "string"},
                "state": {
                    "type": "string",
                    "enum": ["OPEN", "MERGED", "DECLINED", "SUPERSEDED"],
                    "default": "OPEN",
                },
                "pagelen": {"type": "integer", "default": 20},
            },
            "required": ["workspace", "repo_slug"],
        },
    },
    {
        "name": "bitbucket_create_pull_request",
        "description": "Create a pull request in a Bitbucket repository",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "repo_slug": {"type": "string"},
                "title": {"type": "string"},
                "source_branch": {"type": "string"},
                "destination_branch": {"type": "string", "default": "main"},
                "description": {"type": "string", "default": ""},
                "close_source_branch": {"type": "boolean", "default": False},
            },
            "required": ["workspace", "repo_slug", "title", "source_branch"],
        },
    },
    {
        "name": "bitbucket_list_pipelines",
        "description": "List Bitbucket Pipelines for a repository",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "repo_slug": {"type": "string"},
                "pagelen": {"type": "integer", "default": 20},
            },
            "required": ["workspace", "repo_slug"],
        },
    },
]


def _auth_header() -> dict[str, str]:
    username = os.getenv("BITBUCKET_USERNAME", "")
    app_password = os.getenv("BITBUCKET_APP_PASSWORD", "")
    token = base64.b64encode(f"{username}:{app_password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=_API_BASE, headers=_auth_header(), timeout=30.0) as client:
        if tool_name == "bitbucket_list_repos":
            workspace = arguments["workspace"]
            params: dict[str, Any] = {
                "page": arguments.get("page", 1),
                "pagelen": arguments.get("pagelen", 20),
            }
            resp = await client.get(f"/repositories/{workspace}", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "repos": [
                    {
                        "slug": r["slug"],
                        "full_name": r["full_name"],
                        "description": r.get("description"),
                        "is_private": r.get("is_private"),
                        "language": r.get("language"),
                        "links": {"html": r.get("links", {}).get("html", {}).get("href")},
                    }
                    for r in data.get("values", [])
                ],
                "size": data.get("size"),
            }

        elif tool_name == "bitbucket_list_issues":
            workspace = arguments["workspace"]
            slug = arguments["repo_slug"]
            params = {"pagelen": arguments.get("pagelen", 20)}
            if arguments.get("status"):
                params["q"] = f'status="{arguments["status"]}"'
            resp = await client.get(f"/repositories/{workspace}/{slug}/issues", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "issues": [
                    {
                        "id": i["id"],
                        "title": i["title"],
                        "status": i["status"],
                        "kind": i.get("kind"),
                        "priority": i.get("priority"),
                        "created_on": i.get("created_on"),
                    }
                    for i in data.get("values", [])
                ]
            }

        elif tool_name == "bitbucket_create_issue":
            workspace = arguments["workspace"]
            slug = arguments["repo_slug"]
            payload: dict[str, Any] = {
                "title": arguments["title"],
                "content": {"raw": arguments.get("content", "")},
                "kind": arguments.get("kind", "bug"),
                "priority": arguments.get("priority", "major"),
            }
            resp = await client.post(f"/repositories/{workspace}/{slug}/issues", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data["id"],
                "title": data["title"],
                "status": data.get("status"),
                "links": {"html": data.get("links", {}).get("html", {}).get("href")},
            }

        elif tool_name == "bitbucket_list_pull_requests":
            workspace = arguments["workspace"]
            slug = arguments["repo_slug"]
            params = {
                "state": arguments.get("state", "OPEN"),
                "pagelen": arguments.get("pagelen", 20),
            }
            resp = await client.get(f"/repositories/{workspace}/{slug}/pullrequests", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "pull_requests": [
                    {
                        "id": pr["id"],
                        "title": pr["title"],
                        "state": pr["state"],
                        "author": pr.get("author", {}).get("display_name"),
                        "source": pr.get("source", {}).get("branch", {}).get("name"),
                        "destination": pr.get("destination", {}).get("branch", {}).get("name"),
                        "links": {"html": pr.get("links", {}).get("html", {}).get("href")},
                    }
                    for pr in data.get("values", [])
                ]
            }

        elif tool_name == "bitbucket_create_pull_request":
            workspace = arguments["workspace"]
            slug = arguments["repo_slug"]
            payload = {
                "title": arguments["title"],
                "description": arguments.get("description", ""),
                "source": {"branch": {"name": arguments["source_branch"]}},
                "destination": {"branch": {"name": arguments.get("destination_branch", "main")}},
                "close_source_branch": arguments.get("close_source_branch", False),
            }
            resp = await client.post(f"/repositories/{workspace}/{slug}/pullrequests", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data["id"],
                "title": data["title"],
                "state": data.get("state"),
                "links": {"html": data.get("links", {}).get("html", {}).get("href")},
            }

        elif tool_name == "bitbucket_list_pipelines":
            workspace = arguments["workspace"]
            slug = arguments["repo_slug"]
            params = {"pagelen": arguments.get("pagelen", 20), "sort": "-created_on"}
            resp = await client.get(f"/repositories/{workspace}/{slug}/pipelines/", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "pipelines": [
                    {
                        "uuid": p["uuid"],
                        "build_number": p.get("build_number"),
                        "state": p.get("state", {}).get("name"),
                        "result": p.get("state", {}).get("result", {}).get("name"),
                        "created_on": p.get("created_on"),
                        "target": p.get("target", {}).get("ref_name"),
                    }
                    for p in data.get("values", [])
                ]
            }

        else:
            return {"error": f"Unknown tool: {tool_name}"}
