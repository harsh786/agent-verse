"""GitLab MCP server — wraps GitLab REST API in MCP protocol.

Environment variables:
  GITLAB_TOKEN:    Personal access token or OAuth2 token
  GITLAB_BASE_URL: Override for self-hosted GitLab (default: https://gitlab.com)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

GITLAB_BASE_URL = os.getenv("GITLAB_BASE_URL", "https://gitlab.com").rstrip("/")
_API_BASE = f"{GITLAB_BASE_URL}/api/v4"

TOOL_DEFINITIONS = [
    {
        "name": "gitlab_list_projects",
        "description": "List GitLab projects the authenticated user is a member of",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "default": 20},
                "search": {"type": "string", "description": "Filter projects by name"},
            },
        },
    },
    {
        "name": "gitlab_list_issues",
        "description": "List issues in a GitLab project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Numeric project ID or URL-encoded namespace/project"},
                "state": {"type": "string", "enum": ["opened", "closed", "all"], "default": "opened"},
                "per_page": {"type": "integer", "default": 20},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "gitlab_create_issue",
        "description": "Create a new issue in a GitLab project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string", "default": ""},
                "labels": {"type": "string", "description": "Comma-separated label names"},
                "assignee_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["project_id", "title"],
        },
    },
    {
        "name": "gitlab_update_issue",
        "description": "Update an existing issue in a GitLab project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "issue_iid": {"type": "integer", "description": "Issue internal ID"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "state_event": {"type": "string", "enum": ["close", "reopen"]},
                "labels": {"type": "string"},
            },
            "required": ["project_id", "issue_iid"],
        },
    },
    {
        "name": "gitlab_list_merge_requests",
        "description": "List merge requests in a GitLab project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "state": {"type": "string", "enum": ["opened", "closed", "merged", "all"], "default": "opened"},
                "per_page": {"type": "integer", "default": 20},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "gitlab_create_merge_request",
        "description": "Create a merge request in a GitLab project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "title": {"type": "string"},
                "source_branch": {"type": "string"},
                "target_branch": {"type": "string", "default": "main"},
                "description": {"type": "string", "default": ""},
                "remove_source_branch": {"type": "boolean", "default": False},
            },
            "required": ["project_id", "title", "source_branch"],
        },
    },
    {
        "name": "gitlab_get_file",
        "description": "Get file contents from a GitLab repository",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "file_path": {"type": "string", "description": "URL-encoded file path"},
                "ref": {"type": "string", "default": "main"},
            },
            "required": ["project_id", "file_path"],
        },
    },
    {
        "name": "gitlab_list_pipelines",
        "description": "List CI/CD pipelines for a GitLab project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "status": {"type": "string", "enum": ["running", "pending", "success", "failed", "canceled", "skipped", "all"]},
                "per_page": {"type": "integer", "default": 20},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "gitlab_trigger_pipeline",
        "description": "Trigger a new CI/CD pipeline for a GitLab project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "ref": {"type": "string", "description": "Branch or tag name", "default": "main"},
                "variables": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value": {"type": "string"},
                        },
                    },
                    "description": "Pipeline variables",
                },
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "gitlab_add_comment",
        "description": "Add a comment (note) to a GitLab issue",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "issue_iid": {"type": "integer"},
                "body": {"type": "string"},
            },
            "required": ["project_id", "issue_iid", "body"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("GITLAB_TOKEN", "")
    h: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _encode_id(project_id: str) -> str:
    """URL-encode project namespace/slug if it contains a slash."""
    from urllib.parse import quote
    if "/" in project_id:
        return quote(project_id, safe="")
    return project_id


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    import base64

    async with httpx.AsyncClient(base_url=_API_BASE, headers=_headers(), timeout=30.0) as client:
        if tool_name == "gitlab_list_projects":
            params: dict[str, Any] = {
                "membership": "true",
                "per_page": arguments.get("per_page", 20),
            }
            if arguments.get("search"):
                params["search"] = arguments["search"]
            resp = await client.get("/projects", params=params)
            resp.raise_for_status()
            return {
                "projects": [
                    {
                        "id": p["id"],
                        "name": p["name"],
                        "path_with_namespace": p["path_with_namespace"],
                        "description": p.get("description"),
                        "web_url": p.get("web_url"),
                        "default_branch": p.get("default_branch"),
                    }
                    for p in resp.json()
                ]
            }

        elif tool_name == "gitlab_list_issues":
            pid = _encode_id(arguments["project_id"])
            params = {
                "state": arguments.get("state", "opened"),
                "per_page": arguments.get("per_page", 20),
            }
            resp = await client.get(f"/projects/{pid}/issues", params=params)
            resp.raise_for_status()
            return {
                "issues": [
                    {
                        "iid": i["iid"],
                        "title": i["title"],
                        "state": i["state"],
                        "author": i.get("author", {}).get("username"),
                        "web_url": i.get("web_url"),
                        "created_at": i.get("created_at"),
                    }
                    for i in resp.json()
                ]
            }

        elif tool_name == "gitlab_create_issue":
            pid = _encode_id(arguments["project_id"])
            payload: dict[str, Any] = {
                "title": arguments["title"],
                "description": arguments.get("description", ""),
            }
            if arguments.get("labels"):
                payload["labels"] = arguments["labels"]
            if arguments.get("assignee_ids"):
                payload["assignee_ids"] = arguments["assignee_ids"]
            resp = await client.post(f"/projects/{pid}/issues", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"iid": data["iid"], "title": data["title"], "web_url": data.get("web_url")}

        elif tool_name == "gitlab_update_issue":
            pid = _encode_id(arguments["project_id"])
            iid = arguments["issue_iid"]
            payload = {}
            for field in ("title", "description", "state_event", "labels"):
                if field in arguments:
                    payload[field] = arguments[field]
            resp = await client.put(f"/projects/{pid}/issues/{iid}", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"iid": data["iid"], "title": data["title"], "state": data["state"]}

        elif tool_name == "gitlab_list_merge_requests":
            pid = _encode_id(arguments["project_id"])
            params = {
                "state": arguments.get("state", "opened"),
                "per_page": arguments.get("per_page", 20),
            }
            resp = await client.get(f"/projects/{pid}/merge_requests", params=params)
            resp.raise_for_status()
            return {
                "merge_requests": [
                    {
                        "iid": mr["iid"],
                        "title": mr["title"],
                        "state": mr["state"],
                        "source_branch": mr.get("source_branch"),
                        "target_branch": mr.get("target_branch"),
                        "web_url": mr.get("web_url"),
                        "author": mr.get("author", {}).get("username"),
                    }
                    for mr in resp.json()
                ]
            }

        elif tool_name == "gitlab_create_merge_request":
            pid = _encode_id(arguments["project_id"])
            payload = {
                "title": arguments["title"],
                "source_branch": arguments["source_branch"],
                "target_branch": arguments.get("target_branch", "main"),
                "description": arguments.get("description", ""),
                "remove_source_branch": arguments.get("remove_source_branch", False),
            }
            resp = await client.post(f"/projects/{pid}/merge_requests", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"iid": data["iid"], "title": data["title"], "web_url": data.get("web_url")}

        elif tool_name == "gitlab_get_file":
            from urllib.parse import quote
            pid = _encode_id(arguments["project_id"])
            file_path = quote(arguments["file_path"], safe="")
            ref = arguments.get("ref", "main")
            resp = await client.get(
                f"/projects/{pid}/repository/files/{file_path}",
                params={"ref": ref},
            )
            resp.raise_for_status()
            data = resp.json()
            content = ""
            if data.get("encoding") == "base64":
                content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
            else:
                content = data.get("content", "")
            return {
                "file_path": data.get("file_path"),
                "ref": data.get("ref"),
                "content": content,
                "size": data.get("size"),
                "encoding": data.get("encoding"),
            }

        elif tool_name == "gitlab_list_pipelines":
            pid = _encode_id(arguments["project_id"])
            params = {"per_page": arguments.get("per_page", 20)}
            if arguments.get("status") and arguments["status"] != "all":
                params["status"] = arguments["status"]
            resp = await client.get(f"/projects/{pid}/pipelines", params=params)
            resp.raise_for_status()
            return {
                "pipelines": [
                    {
                        "id": p["id"],
                        "status": p["status"],
                        "ref": p.get("ref"),
                        "sha": p.get("sha"),
                        "created_at": p.get("created_at"),
                        "web_url": p.get("web_url"),
                    }
                    for p in resp.json()
                ]
            }

        elif tool_name == "gitlab_trigger_pipeline":
            pid = _encode_id(arguments["project_id"])
            payload = {"ref": arguments.get("ref", "main")}
            if arguments.get("variables"):
                payload["variables"] = arguments["variables"]
            resp = await client.post(f"/projects/{pid}/pipeline", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data["id"],
                "status": data["status"],
                "ref": data.get("ref"),
                "web_url": data.get("web_url"),
            }

        elif tool_name == "gitlab_add_comment":
            pid = _encode_id(arguments["project_id"])
            iid = arguments["issue_iid"]
            payload = {"body": arguments["body"]}
            resp = await client.post(f"/projects/{pid}/issues/{iid}/notes", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"id": data["id"], "body": data.get("body", "")[:200]}

        else:
            return {"error": f"Unknown tool: {tool_name}"}
