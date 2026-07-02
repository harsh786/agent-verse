"""Jira MCP server — full Jira REST API v3 + Agile API integration.

Environment variables:
  JIRA_BASE_URL: Jira instance base URL (e.g. https://mycompany.atlassian.net)
  JIRA_EMAIL: Atlassian account email
  JIRA_API_TOKEN: Atlassian API token
"""
from __future__ import annotations

import base64
import os
from contextlib import suppress
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

JIRA_BASE = os.getenv("JIRA_BASE_URL", "").rstrip("/")


def _absolute_http_url(url: str) -> str:
    stripped = url.strip().rstrip("/")
    if not stripped or "://" in stripped:
        return stripped
    return f"https://{stripped}"

TOOL_DEFINITIONS = [
    {
        "name": "jira_search_issues",
        "description": "Search Jira issues using JQL (Jira Query Language)",
        "parameters": {
            "type": "object",
            "properties": {
                "jql": {
                    "type": "string",
                    "description": "JQL query string, e.g. 'project = MYPROJ AND status = Open'",
                },
                "max_results": {"type": "integer", "default": 50},
                "start_at": {"type": "integer", "default": 0},
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Fields to return. Defaults to summary, status, assignee, "
                        "priority, created, updated."
                    ),
                },
            },
            "required": ["jql"],
        },
    },
    {
        "name": "jira_get_issue",
        "description": "Get a single Jira issue by its key or ID (e.g. PROJ-123)",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id_or_key": {
                    "type": "string",
                    "description": "Issue key like PROJ-123 or numeric ID",
                },
            },
            "required": ["issue_id_or_key"],
        },
    },
    {
        "name": "jira_create_issue",
        "description": "Create a new Jira issue",
        "parameters": {
            "type": "object",
            "properties": {
                "project_key": {"type": "string", "description": "Project key, e.g. PROJ"},
                "summary": {"type": "string"},
                "issue_type": {
                    "type": "string",
                    "default": "Task",
                    "description": "Issue type: Bug, Story, Task, Epic, etc.",
                },
                "description": {"type": "string", "default": ""},
                "priority": {
                    "type": "string",
                    "description": "Priority name: Highest, High, Medium, Low, Lowest",
                },
                "assignee_account_id": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "components": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Component names",
                },
                "story_points": {"type": "number"},
            },
            "required": ["project_key", "summary"],
        },
    },
    {
        "name": "jira_update_issue",
        "description": "Update fields of an existing Jira issue",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id_or_key": {"type": "string"},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string"},
                "assignee_account_id": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "story_points": {"type": "number"},
            },
            "required": ["issue_id_or_key"],
        },
    },
    {
        "name": "jira_add_comment",
        "description": "Add a comment to a Jira issue",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id_or_key": {"type": "string"},
                "body": {"type": "string", "description": "Comment text (plain text)"},
            },
            "required": ["issue_id_or_key", "body"],
        },
    },
    {
        "name": "jira_transition_issue",
        "description": "Transition a Jira issue to a new status (e.g. In Progress, Done)",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id_or_key": {"type": "string"},
                "transition_id": {
                    "type": "string",
                    "description": "Transition ID from jira_get_transitions",
                },
                "comment": {"type": "string", "description": "Optional comment on transition"},
            },
            "required": ["issue_id_or_key", "transition_id"],
        },
    },
    {
        "name": "jira_get_transitions",
        "description": "Get available workflow transitions for a Jira issue",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id_or_key": {"type": "string"},
            },
            "required": ["issue_id_or_key"],
        },
    },
    {
        "name": "jira_assign_issue",
        "description": "Assign a Jira issue to a user by their account ID",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id_or_key": {"type": "string"},
                "account_id": {
                    "type": "string",
                    "description": "Atlassian account ID of assignee, or null to unassign",
                },
            },
            "required": ["issue_id_or_key", "account_id"],
        },
    },
    {
        "name": "jira_list_projects",
        "description": "List all accessible Jira projects",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "default": 50},
                "query": {"type": "string", "description": "Filter projects by name or key"},
            },
        },
    },
    {
        "name": "jira_create_sprint",
        "description": "Create a new sprint on an Agile board",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "integer", "description": "Agile board ID"},
                "name": {"type": "string", "description": "Sprint name"},
                "start_date": {"type": "string", "description": "ISO 8601 start date"},
                "end_date": {"type": "string", "description": "ISO 8601 end date"},
                "goal": {"type": "string", "description": "Sprint goal"},
            },
            "required": ["board_id", "name"],
        },
    },
    {
        "name": "jira_get_board_sprints",
        "description": "Get sprints for a Jira Agile board",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "integer"},
                "state": {
                    "type": "string",
                    "enum": ["active", "future", "closed"],
                    "description": "Filter by sprint state",
                },
            },
            "required": ["board_id"],
        },
    },
]


def _jira_auth() -> dict[str, str]:
    email = os.getenv("JIRA_EMAIL", "")
    token = os.getenv("JIRA_API_TOKEN", "")
    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        return await _call_tool_inner(tool_name, arguments)
    except httpx.HTTPStatusError as exc:
        error_body = ""
        with suppress(Exception):
            error_body = exc.response.text[:500]
        return {
            "error": f"HTTP {exc.response.status_code}: "
            f"{error_body or exc.response.reason_phrase}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        logger.error("call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}


async def _call_tool_inner(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    base = _absolute_http_url(os.getenv("JIRA_BASE_URL", ""))
    if not base:
        return {"error": "JIRA_BASE_URL not configured"}

    headers = _jira_auth()

    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=30.0) as client:
        if tool_name == "jira_search_issues":
            default_fields = [
                "summary",
                "status",
                "assignee",
                "priority",
                "created",
                "updated",
                "issuetype",
            ]
            payload: dict[str, Any] = {
                "jql": arguments["jql"],
                "maxResults": arguments.get("max_results", 50),
                "fields": arguments.get("fields", default_fields),
            }
            resp = await client.post("/rest/api/3/search/jql", json=payload)
            resp.raise_for_status()
            data = resp.json()
            issues = data.get("issues", [])
            return {
                "total": data.get("total", len(issues)),
                "start_at": data.get("startAt", 0),
                "max_results": data.get("maxResults", 50),
                "issues": [
                    {
                        "id": i["id"],
                        "key": i["key"],
                        "summary": i["fields"].get("summary", ""),
                        "status": (i["fields"].get("status") or {}).get("name", ""),
                        "priority": (i["fields"].get("priority") or {}).get("name", ""),
                        "assignee": ((i["fields"].get("assignee") or {}).get("displayName", "")),
                        "issue_type": (i["fields"].get("issuetype") or {}).get("name", ""),
                        "created": i["fields"].get("created", ""),
                        "updated": i["fields"].get("updated", ""),
                    }
                    for i in issues
                ],
            }

        elif tool_name == "jira_get_issue":
            key = arguments["issue_id_or_key"]
            resp = await client.get(f"/rest/api/3/issue/{key}")
            resp.raise_for_status()
            i = resp.json()
            fields = i.get("fields", {})
            return {
                "id": i["id"],
                "key": i["key"],
                "summary": fields.get("summary", ""),
                "description": str(fields.get("description") or ""),
                "status": (fields.get("status") or {}).get("name", ""),
                "priority": (fields.get("priority") or {}).get("name", ""),
                "assignee": ((fields.get("assignee") or {}).get("displayName", "")),
                "reporter": ((fields.get("reporter") or {}).get("displayName", "")),
                "issue_type": (fields.get("issuetype") or {}).get("name", ""),
                "created": fields.get("created", ""),
                "updated": fields.get("updated", ""),
                "labels": fields.get("labels", []),
            }

        elif tool_name == "jira_create_issue":
            fields: dict[str, Any] = {
                "project": {"key": arguments["project_key"]},
                "summary": arguments["summary"],
                "issuetype": {"name": arguments.get("issue_type", "Task")},
            }
            if arguments.get("description"):
                fields["description"] = {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": arguments["description"]}],
                        }
                    ],
                }
            if arguments.get("priority"):
                fields["priority"] = {"name": arguments["priority"]}
            if arguments.get("assignee_account_id"):
                fields["assignee"] = {"accountId": arguments["assignee_account_id"]}
            if arguments.get("labels"):
                fields["labels"] = arguments["labels"]
            if arguments.get("components"):
                fields["components"] = [{"name": c} for c in arguments["components"]]

            resp = await client.post("/rest/api/3/issue", json={"fields": fields})
            resp.raise_for_status()
            data = resp.json()
            return {"id": data["id"], "key": data["key"], "self": data.get("self", "")}

        elif tool_name == "jira_update_issue":
            key = arguments["issue_id_or_key"]
            fields = {}
            if "summary" in arguments:
                fields["summary"] = arguments["summary"]
            if "description" in arguments:
                fields["description"] = {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": arguments["description"]}],
                        }
                    ],
                }
            if "priority" in arguments:
                fields["priority"] = {"name": arguments["priority"]}
            if "assignee_account_id" in arguments:
                fields["assignee"] = {"accountId": arguments["assignee_account_id"]}
            if "labels" in arguments:
                fields["labels"] = arguments["labels"]

            resp = await client.put(f"/rest/api/3/issue/{key}", json={"fields": fields})
            resp.raise_for_status()
            return {"updated": True, "key": key}

        elif tool_name == "jira_add_comment":
            key = arguments["issue_id_or_key"]
            payload = {
                "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": arguments["body"]}],
                        }
                    ],
                }
            }
            resp = await client.post(f"/rest/api/3/issue/{key}/comment", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"comment_id": data.get("id"), "created": data.get("created", "")}

        elif tool_name == "jira_transition_issue":
            key = arguments["issue_id_or_key"]
            payload: dict[str, Any] = {"transition": {"id": arguments["transition_id"]}}
            if arguments.get("comment"):
                payload["update"] = {
                    "comment": [
                        {
                            "add": {
                                "body": {
                                    "type": "doc",
                                    "version": 1,
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [
                                                {"type": "text", "text": arguments["comment"]}
                                            ],
                                        }
                                    ],
                                }
                            }
                        }
                    ]
                }
            resp = await client.post(f"/rest/api/3/issue/{key}/transitions", json=payload)
            resp.raise_for_status()
            return {"transitioned": True, "key": key, "transition_id": arguments["transition_id"]}

        elif tool_name == "jira_get_transitions":
            key = arguments["issue_id_or_key"]
            resp = await client.get(f"/rest/api/3/issue/{key}/transitions")
            resp.raise_for_status()
            data = resp.json()
            return {
                "transitions": [
                    {"id": t["id"], "name": t["name"], "to_status": t.get("to", {}).get("name", "")}
                    for t in data.get("transitions", [])
                ]
            }

        elif tool_name == "jira_assign_issue":
            key = arguments["issue_id_or_key"]
            account_id = arguments["account_id"]
            payload = {"accountId": account_id if account_id != "null" else None}
            resp = await client.put(f"/rest/api/3/issue/{key}/assignee", json=payload)
            resp.raise_for_status()
            return {"assigned": True, "key": key, "account_id": account_id}

        elif tool_name == "jira_list_projects":
            params: dict[str, Any] = {"maxResults": arguments.get("max_results", 50)}
            if arguments.get("query"):
                params["query"] = arguments["query"]
            resp = await client.get("/rest/api/3/project", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "projects": [
                    {
                        "id": p["id"],
                        "key": p["key"],
                        "name": p["name"],
                        "type": p.get("projectTypeKey", ""),
                    }
                    for p in (data if isinstance(data, list) else data.get("values", []))
                ]
            }

        elif tool_name == "jira_create_sprint":
            payload = {
                "name": arguments["name"],
                "originBoardId": arguments["board_id"],
            }
            if arguments.get("start_date"):
                payload["startDate"] = arguments["start_date"]
            if arguments.get("end_date"):
                payload["endDate"] = arguments["end_date"]
            if arguments.get("goal"):
                payload["goal"] = arguments["goal"]
            resp = await client.post("/rest/agile/1.0/sprint", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "sprint_id": data.get("id"),
                "name": data.get("name"),
                "state": data.get("state"),
            }

        elif tool_name == "jira_get_board_sprints":
            board_id = arguments["board_id"]
            params = {}
            if arguments.get("state"):
                params["state"] = arguments["state"]
            resp = await client.get(f"/rest/agile/1.0/board/{board_id}/sprint", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "sprints": [
                    {
                        "id": s["id"],
                        "name": s["name"],
                        "state": s.get("state", ""),
                        "start_date": s.get("startDate", ""),
                        "end_date": s.get("endDate", ""),
                        "goal": s.get("goal", ""),
                    }
                    for s in data.get("values", [])
                ]
            }

        else:
            return {"error": f"Unknown tool: {tool_name}"}
