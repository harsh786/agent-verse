"""GitHub MCP server wrapper — wraps GitHub REST API in MCP protocol.

Credentials come ONLY from the calling tenant's connector (``credentials``:
``token`` / ``api_token`` / ``password``). The platform's GITHUB_TOKEN is never
used: it made every tenant's agent act with the platform's GitHub identity.

Environment variables:
  GITHUB_BASE_URL: Override for GitHub Enterprise (default: https://api.github.com)
"""

from __future__ import annotations

import contextlib
from typing import Any
from urllib.parse import urlparse

import httpx

from app.mcp.servers.credentials import in_tenant_scope, tenant_getenv
from app.observability.logging import get_logger

logger = get_logger(__name__)


TOOL_DEFINITIONS = [
    {
        "name": "github_list_repos",
        "description": "List repositories for a user or organization",
        "parameters": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "per_page": {"type": "integer", "default": 30},
            },
            "required": ["owner"],
        },
    },
    {
        "name": "github_get_file",
        "description": "Get file contents from a GitHub repository",
        "parameters": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "path": {"type": "string"},
                "ref": {"type": "string", "default": "main"},
            },
            "required": ["owner", "repo", "path"],
        },
    },
    {
        "name": "github_list_issues",
        "description": "List issues for a repository",
        "parameters": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "state": {
                    "type": "string",
                    "enum": ["open", "closed", "all"],
                    "default": "open",
                },
                "per_page": {"type": "integer", "default": 20},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "github_create_issue",
        "description": "Create a new issue in a repository",
        "parameters": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string", "default": ""},
                "labels": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["owner", "repo", "title"],
        },
    },
    {
        "name": "github_create_pr",
        "description": "Create a pull request",
        "parameters": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "title": {"type": "string"},
                "head": {"type": "string"},
                "base": {"type": "string", "default": "main"},
                "body": {"type": "string", "default": ""},
            },
            "required": ["owner", "repo", "title", "head"],
        },
    },
    {
        "name": "github_search_code",
        "description": "Search code across GitHub repositories",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "per_page": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
]


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    creds = credentials or {}
    # No tenant_getenv("GITHUB_TOKEN") fallback (confused deputy — same fix as
    # app/knowledge/ingestors/github_ingestor.py): without a tenant token the
    # call is anonymous. The unused env-reading _headers() helper is removed too.
    token = creds.get("token") or creds.get("api_token") or creds.get("password") or ""
    tenant_base = str(creds.get("url") or creds.get("base_url") or "")
    base_url = tenant_base or tenant_getenv("GITHUB_BASE_URL", "https://api.github.com")
    from app.net.ssrf_guard import (
        SSRFError,
        assert_public_url_async,
        private_access_networks,
        public_async_client,
    )

    # An operator GITHUB_BASE_URL (GHES on the LAN) is trusted config: its own
    # host is the connect-time allowlist. A tenant URL gets none.
    pin_allowlist: list[str] | None = None
    if tenant_base:
        # Tenant-supplied (GitHub Enterprise) URL was used unchecked — SSRF.
        try:
            await assert_public_url_async(
                base_url, context="github connector", allowed_networks=private_access_networks()
            )
        except SSRFError:
            return {"error": "GitHub URL blocked by SSRF guard"}
    elif not in_tenant_scope() and tenant_getenv("GITHUB_BASE_URL"):
        pin_allowlist = [(urlparse(base_url).hostname or "").lower()]
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        # Pinned to the checked address: a plain client re-resolved the name.
        async with public_async_client(
            allowed_domains=pin_allowlist,
        allowed_networks=private_access_networks(),
        base_url=base_url, headers=headers, timeout=30.0
        ) as client:
            return await _dispatch_github_tool(tool_name, arguments, client)
    except httpx.HTTPStatusError as exc:
        error_body = ""
        with contextlib.suppress(Exception):
            error_body = exc.response.text[:500]
        return {
            "error": f"HTTP {exc.response.status_code}: {error_body or exc.response.reason_phrase}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        logger.error("call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}


async def _dispatch_github_tool(
    tool_name: str, arguments: dict[str, Any], client: httpx.AsyncClient
) -> dict[str, Any]:
    if tool_name == "github_list_repos":
        owner = arguments["owner"]
        resp = await client.get(
            f"/users/{owner}/repos",
            params={"per_page": arguments.get("per_page", 30)},
        )
        resp.raise_for_status()
        return {
            "repos": [
                {
                    "name": r["name"],
                    "full_name": r["full_name"],
                    "description": r.get("description"),
                    "url": r["html_url"],
                }
                for r in resp.json()
            ]
        }

    elif tool_name == "github_get_file":
        owner = arguments["owner"]
        repo = arguments["repo"]
        path = arguments["path"]
        ref = arguments.get("ref", "main")
        resp = await client.get(
            f"/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
        )
        resp.raise_for_status()
        data = resp.json()
        import base64

        content = (
            base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
            if data.get("encoding") == "base64"
            else data.get("content", "")
        )
        return {
            "path": data["path"],
            "sha": data["sha"],
            "content": content,
            "size": data["size"],
        }

    elif tool_name == "github_list_issues":
        owner, repo = arguments["owner"], arguments["repo"]
        resp = await client.get(
            f"/repos/{owner}/{repo}/issues",
            params={
                "state": arguments.get("state", "open"),
                "per_page": arguments.get("per_page", 20),
            },
        )
        resp.raise_for_status()
        return {
            "issues": [
                {
                    "number": i["number"],
                    "title": i["title"],
                    "state": i["state"],
                    "url": i["html_url"],
                    "body": (i.get("body") or "")[:500],
                }
                for i in resp.json()
            ]
        }

    elif tool_name == "github_create_issue":
        owner, repo = arguments["owner"], arguments["repo"]
        payload = {
            "title": arguments["title"],
            "body": arguments.get("body", ""),
            "labels": arguments.get("labels", []),
        }
        resp = await client.post(f"/repos/{owner}/{repo}/issues", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return {
            "issue_number": data["number"],
            "url": data["html_url"],
            "title": data["title"],
        }

    elif tool_name == "github_create_pr":
        owner, repo = arguments["owner"], arguments["repo"]
        payload = {
            "title": arguments["title"],
            "head": arguments["head"],
            "base": arguments.get("base", "main"),
            "body": arguments.get("body", ""),
        }
        resp = await client.post(f"/repos/{owner}/{repo}/pulls", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return {
            "pr_number": data["number"],
            "url": data["html_url"],
            "title": data["title"],
        }

    elif tool_name == "github_search_code":
        resp = await client.get(
            "/search/code",
            params={
                "q": arguments["query"],
                "per_page": arguments.get("per_page", 10),
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "total_count": data["total_count"],
            "items": [
                {
                    "name": i["name"],
                    "path": i["path"],
                    "url": i["html_url"],
                    "repo": i["repository"]["full_name"],
                }
                for i in data.get("items", [])
            ],
        }

    else:
        return {"error": f"Unknown tool: {tool_name}"}
