"""Azure DevOps MCP server — manage repos, PRs, work items, and pipelines.

Environment variables:
  AZURE_DEVOPS_TOKEN:   Personal access token (PAT)
  AZURE_DEVOPS_ORG:     Organization name (e.g. mycompany)
  AZURE_DEVOPS_PROJECT: Default project name
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

_API_VERSION = "7.1"


def _org() -> str:
    return os.getenv("AZURE_DEVOPS_ORG", "")


def _project() -> str:
    return os.getenv("AZURE_DEVOPS_PROJECT", "")


def _base_url() -> str:
    org = _org()
    return f"https://dev.azure.com/{org}"


def _headers() -> dict[str, str]:
    token = os.getenv("AZURE_DEVOPS_TOKEN", "")
    # Azure DevOps uses Basic auth with any username and PAT as password
    encoded = base64.b64encode(f":{token}".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "azure_list_repos",
        "description": "List Git repositories in an Azure DevOps project",
        "parameters": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name (overrides AZURE_DEVOPS_PROJECT)"},
            },
        },
    },
    {
        "name": "azure_list_pull_requests",
        "description": "List pull requests in Azure DevOps, optionally filtered by repository",
        "parameters": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "repository_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["active", "abandoned", "completed", "all"],
                    "default": "active",
                },
                "top": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "azure_create_pull_request",
        "description": "Create a pull request in Azure DevOps",
        "parameters": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "repository_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string", "default": ""},
                "source_ref_name": {"type": "string", "description": "Source branch (e.g. refs/heads/feature)"},
                "target_ref_name": {"type": "string", "description": "Target branch (e.g. refs/heads/main)"},
                "reviewers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Reviewer UUIDs or email addresses",
                },
            },
            "required": ["repository_id", "title", "source_ref_name", "target_ref_name"],
        },
    },
    {
        "name": "azure_list_work_items",
        "description": "Query Azure DevOps work items using WIQL",
        "parameters": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "wiql_query": {
                    "type": "string",
                    "description": "WIQL query string (e.g. SELECT [System.Id],[System.Title] FROM WorkItems WHERE [System.TeamProject] = @project)",
                },
                "top": {"type": "integer", "default": 50},
            },
            "required": ["wiql_query"],
        },
    },
    {
        "name": "azure_create_work_item",
        "description": "Create a work item (Bug, Task, User Story, etc.) in Azure DevOps",
        "parameters": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "work_item_type": {
                    "type": "string",
                    "description": "Work item type (e.g. Bug, Task, User Story, Epic)",
                    "default": "Task",
                },
                "title": {"type": "string"},
                "description": {"type": "string", "default": ""},
                "assigned_to": {"type": "string", "description": "Email address or display name"},
                "priority": {"type": "integer", "enum": [1, 2, 3, 4]},
                "area_path": {"type": "string"},
                "iteration_path": {"type": "string"},
                "tags": {"type": "string", "description": "Semicolon-separated tags"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "azure_list_pipelines",
        "description": "List Azure DevOps pipelines in a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "top": {"type": "integer", "default": 50},
                "order_by": {"type": "string", "default": "name"},
            },
        },
    },
    {
        "name": "azure_run_pipeline",
        "description": "Trigger a run of an Azure DevOps pipeline",
        "parameters": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "pipeline_id": {"type": "integer"},
                "branch": {"type": "string", "default": "main"},
                "variables": {
                    "type": "object",
                    "description": "Pipeline variables as key-value pairs",
                    "additionalProperties": {"type": "string"},
                },
                "stages_to_skip": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["pipeline_id"],
        },
    },
]


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
    base = _base_url()
    if not _org():
        return {"error": "AZURE_DEVOPS_ORG not configured"}

    proj = arguments.get("project") or _project()

    async with httpx.AsyncClient(headers=_headers(), timeout=30.0) as client:
        if tool_name == "azure_list_repos":
            url = f"{base}/{proj}/_apis/git/repositories"
            resp = await client.get(url, params={"api-version": _API_VERSION})
            resp.raise_for_status()
            data = resp.json()
            return {
                "repositories": [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "default_branch": r.get("defaultBranch"),
                        "remote_url": r.get("remoteUrl"),
                        "size": r.get("size"),
                        "project": r.get("project", {}).get("name"),
                    }
                    for r in data.get("value", [])
                ],
                "count": data.get("count", 0),
            }

        elif tool_name == "azure_list_pull_requests":
            if arguments.get("repository_id"):
                repo_id = arguments["repository_id"]
                url = f"{base}/{proj}/_apis/git/repositories/{repo_id}/pullrequests"
            else:
                url = f"{base}/{proj}/_apis/git/pullrequests"
            params: dict[str, Any] = {
                "api-version": _API_VERSION,
                "searchCriteria.status": arguments.get("status", "active"),
                "$top": arguments.get("top", 20),
            }
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "pull_requests": [
                    {
                        "id": pr["pullRequestId"],
                        "title": pr["title"],
                        "status": pr["status"],
                        "created_by": pr.get("createdBy", {}).get("displayName"),
                        "source_ref": pr.get("sourceRefName"),
                        "target_ref": pr.get("targetRefName"),
                        "repository": pr.get("repository", {}).get("name"),
                        "creation_date": pr.get("creationDate"),
                    }
                    for pr in data.get("value", [])
                ],
                "count": data.get("count", 0),
            }

        elif tool_name == "azure_create_pull_request":
            repo_id = arguments["repository_id"]
            url = f"{base}/{proj}/_apis/git/repositories/{repo_id}/pullrequests"
            payload: dict[str, Any] = {
                "title": arguments["title"],
                "description": arguments.get("description", ""),
                "sourceRefName": arguments["source_ref_name"],
                "targetRefName": arguments["target_ref_name"],
            }
            if arguments.get("reviewers"):
                payload["reviewers"] = [{"id": r} for r in arguments["reviewers"]]
            resp = await client.post(url, json=payload, params={"api-version": _API_VERSION})
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data.get("pullRequestId"),
                "title": data.get("title"),
                "status": data.get("status"),
                "url": data.get("url"),
                "repository": data.get("repository", {}).get("name"),
            }

        elif tool_name == "azure_list_work_items":
            url = f"{base}/{proj}/_apis/wit/wiql"
            payload = {"query": arguments["wiql_query"]}
            params = {"api-version": _API_VERSION, "$top": arguments.get("top", 50)}
            resp = await client.post(url, json=payload, params=params)
            resp.raise_for_status()
            data = resp.json()
            work_item_refs = data.get("workItems", [])
            if not work_item_refs:
                return {"work_items": [], "count": 0}
            # Fetch work item details in batch (max 200)
            ids = [str(wi["id"]) for wi in work_item_refs[: arguments.get("top", 50)]]
            details_url = f"{base}/{proj}/_apis/wit/workitems"
            details_resp = await client.get(
                details_url,
                params={
                    "ids": ",".join(ids),
                    "fields": "System.Id,System.Title,System.State,System.AssignedTo,System.WorkItemType,System.Tags",
                    "api-version": _API_VERSION,
                },
            )
            details_resp.raise_for_status()
            details = details_resp.json()
            return {
                "work_items": [
                    {
                        "id": wi["id"],
                        "title": wi.get("fields", {}).get("System.Title"),
                        "state": wi.get("fields", {}).get("System.State"),
                        "type": wi.get("fields", {}).get("System.WorkItemType"),
                        "assigned_to": (wi.get("fields", {}).get("System.AssignedTo") or {}).get("displayName"),
                        "tags": wi.get("fields", {}).get("System.Tags", ""),
                    }
                    for wi in details.get("value", [])
                ],
                "count": len(ids),
            }

        elif tool_name == "azure_create_work_item":
            work_item_type = arguments.get("work_item_type", "Task")
            url = f"{base}/{proj}/_apis/wit/workitems/${work_item_type}"
            # Azure DevOps work items use JSON Patch format
            ops = [
                {"op": "add", "path": "/fields/System.Title", "value": arguments["title"]},
            ]
            if arguments.get("description"):
                ops.append({"op": "add", "path": "/fields/System.Description", "value": arguments["description"]})
            if arguments.get("assigned_to"):
                ops.append({"op": "add", "path": "/fields/System.AssignedTo", "value": arguments["assigned_to"]})
            if arguments.get("priority"):
                ops.append({"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": arguments["priority"]})
            if arguments.get("area_path"):
                ops.append({"op": "add", "path": "/fields/System.AreaPath", "value": arguments["area_path"]})
            if arguments.get("iteration_path"):
                ops.append({"op": "add", "path": "/fields/System.IterationPath", "value": arguments["iteration_path"]})
            if arguments.get("tags"):
                ops.append({"op": "add", "path": "/fields/System.Tags", "value": arguments["tags"]})
            resp = await client.post(
                url,
                content=__import__("json").dumps(ops),
                headers={**_headers(), "Content-Type": "application/json-patch+json"},
                params={"api-version": _API_VERSION},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data.get("id"),
                "title": data.get("fields", {}).get("System.Title"),
                "state": data.get("fields", {}).get("System.State"),
                "type": data.get("fields", {}).get("System.WorkItemType"),
                "url": data.get("url"),
            }

        elif tool_name == "azure_list_pipelines":
            url = f"{base}/{proj}/_apis/pipelines"
            params = {
                "api-version": _API_VERSION,
                "$top": arguments.get("top", 50),
                "orderBy": arguments.get("order_by", "name"),
            }
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "pipelines": [
                    {
                        "id": p["id"],
                        "name": p["name"],
                        "folder": p.get("folder"),
                        "revision": p.get("revision"),
                        "url": p.get("url"),
                    }
                    for p in data.get("value", [])
                ],
                "count": data.get("count", 0),
            }

        elif tool_name == "azure_run_pipeline":
            pipeline_id = arguments["pipeline_id"]
            url = f"{base}/{proj}/_apis/pipelines/{pipeline_id}/runs"
            branch = arguments.get("branch", "main")
            payload = {
                "resources": {
                    "repositories": {
                        "self": {
                            "refName": f"refs/heads/{branch}",
                        }
                    }
                }
            }
            if arguments.get("variables"):
                payload["variables"] = {
                    k: {"value": v, "isSecret": False}
                    for k, v in arguments["variables"].items()
                }
            if arguments.get("stages_to_skip"):
                payload["stagesToSkip"] = arguments["stages_to_skip"]
            resp = await client.post(url, json=payload, params={"api-version": _API_VERSION})
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data.get("id"),
                "name": data.get("name"),
                "state": data.get("state"),
                "result": data.get("result"),
                "url": data.get("url"),
                "created_date": data.get("createdDate"),
            }

        else:
            return {"error": f"Unknown tool: {tool_name}"}
