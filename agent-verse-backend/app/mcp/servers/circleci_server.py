"""CircleCI MCP server — pipelines, workflows, jobs, and artifacts via API v2.

Environment variables:
  CIRCLECI_TOKEN: CircleCI personal API token
  CIRCLECI_ORG_SLUG: Default org slug, e.g. 'gh/myorg' (used when project_slug omits org)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

CIRCLECI_BASE_URL = "https://circleci.com/api/v2"

TOOL_DEFINITIONS = [
    {
        "name": "circleci_list_pipelines",
        "description": "List recent pipelines for a CircleCI project.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_slug": {
                    "type": "string",
                    "description": "Project slug, e.g. 'gh/myorg/myrepo'",
                },
                "branch": {"type": "string", "description": "Filter by branch name"},
                "page_token": {"type": "string", "description": "Pagination token"},
            },
            "required": ["project_slug"],
        },
    },
    {
        "name": "circleci_get_pipeline",
        "description": "Get details of a specific CircleCI pipeline by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "pipeline_id": {"type": "string", "description": "Pipeline UUID"},
            },
            "required": ["pipeline_id"],
        },
    },
    {
        "name": "circleci_trigger_pipeline",
        "description": "Trigger a new CircleCI pipeline for a project branch or tag.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_slug": {
                    "type": "string",
                    "description": "Project slug, e.g. 'gh/myorg/myrepo'",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch to run the pipeline on (mutually exclusive with tag)",
                },
                "tag": {
                    "type": "string",
                    "description": "Git tag to run the pipeline on",
                },
                "parameters": {
                    "type": "object",
                    "description": "Pipeline parameters to pass as key/value pairs",
                },
            },
            "required": ["project_slug"],
        },
    },
    {
        "name": "circleci_list_workflows",
        "description": "List workflows for a given CircleCI pipeline.",
        "parameters": {
            "type": "object",
            "properties": {
                "pipeline_id": {"type": "string", "description": "Pipeline UUID"},
                "page_token": {"type": "string"},
            },
            "required": ["pipeline_id"],
        },
    },
    {
        "name": "circleci_get_workflow",
        "description": "Get details of a specific CircleCI workflow by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Workflow UUID"},
            },
            "required": ["workflow_id"],
        },
    },
    {
        "name": "circleci_cancel_workflow",
        "description": "Cancel a running CircleCI workflow.",
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Workflow UUID"},
            },
            "required": ["workflow_id"],
        },
    },
    {
        "name": "circleci_list_jobs",
        "description": "List all jobs in a CircleCI workflow.",
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Workflow UUID"},
                "page_token": {"type": "string"},
            },
            "required": ["workflow_id"],
        },
    },
    {
        "name": "circleci_get_job",
        "description": "Get details of a specific job in a CircleCI project.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_slug": {
                    "type": "string",
                    "description": "Project slug, e.g. 'gh/myorg/myrepo'",
                },
                "job_number": {
                    "type": "integer",
                    "description": "Job number (not UUID)",
                },
            },
            "required": ["project_slug", "job_number"],
        },
    },
    {
        "name": "circleci_get_job_artifacts",
        "description": "List artifacts produced by a CircleCI job.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_slug": {
                    "type": "string",
                    "description": "Project slug, e.g. 'gh/myorg/myrepo'",
                },
                "job_number": {"type": "integer", "description": "Job number"},
            },
            "required": ["project_slug", "job_number"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("CIRCLECI_TOKEN", "")
    return {
        "Circle-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    token = os.getenv("CIRCLECI_TOKEN", "")
    if not token:
        return {"error": "CIRCLECI_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=CIRCLECI_BASE_URL, headers=_headers(), timeout=30.0
        ) as client:
            if tool_name == "circleci_list_pipelines":
                project_slug = arguments["project_slug"]
                params: dict[str, Any] = {}
                if arguments.get("branch"):
                    params["branch"] = arguments["branch"]
                if arguments.get("page_token"):
                    params["page-token"] = arguments["page_token"]
                resp = await client.get(
                    f"/project/{project_slug}/pipeline", params=params
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "next_page_token": data.get("next_page_token"),
                    "pipelines": [
                        {
                            "id": p["id"],
                            "number": p.get("number"),
                            "state": p.get("state", ""),
                            "created_at": p.get("created_at", ""),
                            "trigger_type": (p.get("trigger") or {}).get("type", ""),
                            "vcs_branch": (p.get("vcs") or {}).get("branch", ""),
                            "vcs_revision": (p.get("vcs") or {}).get("revision", ""),
                        }
                        for p in data.get("items", [])
                    ],
                }

            elif tool_name == "circleci_get_pipeline":
                resp = await client.get(f"/pipeline/{arguments['pipeline_id']}")
                resp.raise_for_status()
                p = resp.json()
                return {
                    "id": p["id"],
                    "number": p.get("number"),
                    "state": p.get("state", ""),
                    "created_at": p.get("created_at", ""),
                    "updated_at": p.get("updated_at", ""),
                    "trigger_type": (p.get("trigger") or {}).get("type", ""),
                    "vcs_branch": (p.get("vcs") or {}).get("branch", ""),
                    "vcs_revision": (p.get("vcs") or {}).get("revision", ""),
                    "errors": p.get("errors", []),
                }

            elif tool_name == "circleci_trigger_pipeline":
                project_slug = arguments["project_slug"]
                payload: dict[str, Any] = {}
                if arguments.get("branch"):
                    payload["branch"] = arguments["branch"]
                if arguments.get("tag"):
                    payload["tag"] = arguments["tag"]
                if arguments.get("parameters"):
                    payload["parameters"] = arguments["parameters"]
                resp = await client.post(
                    f"/project/{project_slug}/pipeline", json=payload
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "id": data["id"],
                    "number": data.get("number"),
                    "state": data.get("state", ""),
                    "created_at": data.get("created_at", ""),
                }

            elif tool_name == "circleci_list_workflows":
                pipeline_id = arguments["pipeline_id"]
                params = {}
                if arguments.get("page_token"):
                    params["page-token"] = arguments["page_token"]
                resp = await client.get(
                    f"/pipeline/{pipeline_id}/workflow", params=params
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "next_page_token": data.get("next_page_token"),
                    "workflows": [
                        {
                            "id": w["id"],
                            "name": w.get("name", ""),
                            "status": w.get("status", ""),
                            "created_at": w.get("created_at", ""),
                            "stopped_at": w.get("stopped_at", ""),
                            "pipeline_number": w.get("pipeline_number"),
                        }
                        for w in data.get("items", [])
                    ],
                }

            elif tool_name == "circleci_get_workflow":
                resp = await client.get(f"/workflow/{arguments['workflow_id']}")
                resp.raise_for_status()
                w = resp.json()
                return {
                    "id": w["id"],
                    "name": w.get("name", ""),
                    "status": w.get("status", ""),
                    "created_at": w.get("created_at", ""),
                    "stopped_at": w.get("stopped_at", ""),
                    "pipeline_id": w.get("pipeline_id", ""),
                    "pipeline_number": w.get("pipeline_number"),
                    "project_slug": w.get("project_slug", ""),
                }

            elif tool_name == "circleci_cancel_workflow":
                workflow_id = arguments["workflow_id"]
                resp = await client.post(f"/workflow/{workflow_id}/cancel")
                resp.raise_for_status()
                return {"cancelled": True, "workflow_id": workflow_id}

            elif tool_name == "circleci_list_jobs":
                workflow_id = arguments["workflow_id"]
                params = {}
                if arguments.get("page_token"):
                    params["page-token"] = arguments["page_token"]
                resp = await client.get(f"/workflow/{workflow_id}/job", params=params)
                resp.raise_for_status()
                data = resp.json()
                return {
                    "next_page_token": data.get("next_page_token"),
                    "jobs": [
                        {
                            "id": j.get("id", ""),
                            "name": j.get("name", ""),
                            "status": j.get("status", ""),
                            "type": j.get("type", ""),
                            "job_number": j.get("job_number"),
                            "started_at": j.get("started_at", ""),
                            "stopped_at": j.get("stopped_at", ""),
                            "dependencies": j.get("dependencies", []),
                        }
                        for j in data.get("items", [])
                    ],
                }

            elif tool_name == "circleci_get_job":
                project_slug = arguments["project_slug"]
                job_number = arguments["job_number"]
                resp = await client.get(f"/project/{project_slug}/job/{job_number}")
                resp.raise_for_status()
                j = resp.json()
                return {
                    "number": j.get("number"),
                    "name": j.get("name", ""),
                    "status": j.get("status", ""),
                    "type": j.get("type", ""),
                    "created_at": j.get("created_at", ""),
                    "started_at": j.get("started_at", ""),
                    "stopped_at": j.get("stopped_at", ""),
                    "duration": j.get("duration"),
                    "executor": (j.get("executor") or {}).get("type", ""),
                    "web_url": j.get("web_url", ""),
                }

            elif tool_name == "circleci_get_job_artifacts":
                project_slug = arguments["project_slug"]
                job_number = arguments["job_number"]
                resp = await client.get(
                    f"/project/{project_slug}/{job_number}/artifacts"
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "artifacts": [
                        {
                            "path": a.get("path", ""),
                            "node_index": a.get("node_index", 0),
                            "url": a.get("url", ""),
                        }
                        for a in data.get("items", [])
                    ]
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.text[:500]
        except Exception:
            pass
        return {
            "error": f"HTTP {exc.response.status_code}: {error_body}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        logger.error("call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}
