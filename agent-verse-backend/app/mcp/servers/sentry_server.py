"""Sentry MCP server — manage issues, events, releases, and run Discover queries.

Environment:
  SENTRY_AUTH_TOKEN: Sentry internal integration or user auth token
  SENTRY_ORG_SLUG:   Organization slug (e.g. my-company)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

SENTRY_BASE = "https://sentry.io/api/0"

TOOL_DEFINITIONS = [
    {
        "name": "sentry_list_projects",
        "description": "List all projects in the Sentry organization",
        "parameters": {
            "type": "object",
            "properties": {
                "cursor": {"type": "string", "description": "Pagination cursor"},
            },
        },
    },
    {
        "name": "sentry_list_issues",
        "description": "List issues for a Sentry project with optional query filters",
        "parameters": {
            "type": "object",
            "properties": {
                "project_slug": {"type": "string"},
                "query": {"type": "string", "default": "is:unresolved", "description": "Issue search query"},
                "limit": {"type": "integer", "default": 25},
                "cursor": {"type": "string"},
            },
            "required": ["project_slug"],
        },
    },
    {
        "name": "sentry_get_issue",
        "description": "Get details for a specific Sentry issue",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "name": "sentry_update_issue",
        "description": "Update a Sentry issue status, assignment, or other fields",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["resolved", "resolvedInNextRelease", "unresolved", "ignored"],
                },
                "assignedTo": {"type": "string", "description": "Username or team slug to assign"},
                "hasSeen": {"type": "boolean"},
                "isBookmarked": {"type": "boolean"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "name": "sentry_list_events",
        "description": "List events for a specific Sentry issue",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "full": {"type": "boolean", "default": False, "description": "Include full event details"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "name": "sentry_create_release",
        "description": "Create a new release in Sentry for deployment tracking",
        "parameters": {
            "type": "object",
            "properties": {
                "version": {"type": "string", "description": "Release version string"},
                "projects": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Project slugs to associate with this release",
                },
                "ref": {"type": "string", "description": "Commit ref/SHA"},
                "url": {"type": "string", "description": "URL linking to the deployment"},
            },
            "required": ["version", "projects"],
        },
    },
    {
        "name": "sentry_query",
        "description": "Run a Sentry Discover query to analyze event data",
        "parameters": {
            "type": "object",
            "properties": {
                "project_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Project IDs to query",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fields to return (e.g. ['title', 'count()', 'last_seen()'])",
                },
                "query": {"type": "string", "default": ""},
                "start": {"type": "string", "description": "ISO8601 start time"},
                "end": {"type": "string", "description": "ISO8601 end time"},
                "statsPeriod": {"type": "string", "default": "14d"},
                "orderby": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["fields"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("SENTRY_AUTH_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("SENTRY_AUTH_TOKEN", "")
    org = os.getenv("SENTRY_ORG_SLUG", "")

    if not token:
        return {"error": "SENTRY_AUTH_TOKEN not configured"}
    if not org and tool_name in ("sentry_list_projects", "sentry_list_issues", "sentry_create_release", "sentry_query"):
        return {"error": "SENTRY_ORG_SLUG not configured"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            hdrs = _headers()

            if tool_name == "sentry_list_projects":
                params: dict[str, Any] = {}
                if cursor := arguments.get("cursor"):
                    params["cursor"] = cursor
                resp = await client.get(
                    f"{SENTRY_BASE}/organizations/{org}/projects/",
                    params=params,
                    headers=hdrs,
                )
                resp.raise_for_status()
                return {
                    "projects": [
                        {"id": p["id"], "slug": p["slug"], "name": p["name"]}
                        for p in resp.json()
                    ]
                }

            elif tool_name == "sentry_list_issues":
                project = arguments["project_slug"]
                params = {
                    "query": arguments.get("query", "is:unresolved"),
                    "limit": arguments.get("limit", 25),
                }
                if cursor := arguments.get("cursor"):
                    params["cursor"] = cursor
                resp = await client.get(
                    f"{SENTRY_BASE}/projects/{org}/{project}/issues/",
                    params=params,
                    headers=hdrs,
                )
                resp.raise_for_status()
                issues = resp.json()
                return {
                    "issues": [
                        {
                            "id": i["id"],
                            "title": i["title"],
                            "status": i["status"],
                            "level": i.get("level"),
                            "count": i.get("count"),
                            "lastSeen": i.get("lastSeen"),
                            "firstSeen": i.get("firstSeen"),
                        }
                        for i in issues
                    ]
                }

            elif tool_name == "sentry_get_issue":
                resp = await client.get(
                    f"{SENTRY_BASE}/issues/{arguments['issue_id']}/",
                    headers=hdrs,
                )
                resp.raise_for_status()
                return resp.json()

            elif tool_name == "sentry_update_issue":
                issue_id = arguments["issue_id"]
                payload: dict[str, Any] = {}
                for field in ("status", "assignedTo", "hasSeen", "isBookmarked"):
                    if field in arguments:
                        payload[field] = arguments[field]
                resp = await client.put(
                    f"{SENTRY_BASE}/issues/{issue_id}/",
                    json=payload,
                    headers=hdrs,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "id": data.get("id"),
                    "status": data.get("status"),
                    "assignedTo": data.get("assignedTo"),
                }

            elif tool_name == "sentry_list_events":
                issue_id = arguments["issue_id"]
                params = {"limit": arguments.get("limit", 10)}
                if arguments.get("full"):
                    params["full"] = "true"
                resp = await client.get(
                    f"{SENTRY_BASE}/issues/{issue_id}/events/",
                    params=params,
                    headers=hdrs,
                )
                resp.raise_for_status()
                return {"events": resp.json()}

            elif tool_name == "sentry_create_release":
                payload = {
                    "version": arguments["version"],
                    "projects": arguments["projects"],
                }
                if ref := arguments.get("ref"):
                    payload["ref"] = ref
                if url := arguments.get("url"):
                    payload["url"] = url
                resp = await client.post(
                    f"{SENTRY_BASE}/organizations/{org}/releases/",
                    json=payload,
                    headers=hdrs,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "version": data.get("version"),
                    "dateCreated": data.get("dateCreated"),
                    "projects": [p["slug"] for p in data.get("projects", [])],
                }

            elif tool_name == "sentry_query":
                payload = {
                    "fields": arguments["fields"],
                    "query": arguments.get("query", ""),
                    "limit": arguments.get("limit", 50),
                    "statsPeriod": arguments.get("statsPeriod", "14d"),
                }
                if project_ids := arguments.get("project_ids"):
                    payload["project"] = project_ids
                if start := arguments.get("start"):
                    payload["start"] = start
                if end := arguments.get("end"):
                    payload["end"] = end
                if orderby := arguments.get("orderby"):
                    payload["orderby"] = orderby
                resp = await client.post(
                    f"{SENTRY_BASE}/organizations/{org}/events/",
                    json=payload,
                    headers=hdrs,
                )
                resp.raise_for_status()
                return resp.json()

            else:
                return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("sentry_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
