"""Linear MCP server — Linear GraphQL API integration.

Environment variables:
  LINEAR_API_KEY: Linear personal API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

LINEAR_GQL = "https://api.linear.app/graphql"

TOOL_DEFINITIONS = [
    {
        "name": "linear_list_issues",
        "description": "List Linear issues with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Filter by team ID"},
                "assignee_id": {"type": "string", "description": "Filter by assignee user ID"},
                "state": {"type": "string", "description": "Filter by state name (e.g. Todo, In Progress, Done)"},
                "priority": {
                    "type": "integer",
                    "description": "Filter by priority (0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low)",
                },
                "first": {"type": "integer", "default": 50, "description": "Max number of issues to return"},
            },
        },
    },
    {
        "name": "linear_get_issue",
        "description": "Get a single Linear issue by its ID",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string", "description": "Linear issue UUID"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "name": "linear_create_issue",
        "description": "Create a new Linear issue",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "team_id": {"type": "string", "description": "Team ID where the issue will be created"},
                "description": {"type": "string", "default": ""},
                "priority": {
                    "type": "integer",
                    "description": "Priority: 0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low",
                },
                "assignee_id": {"type": "string"},
                "state_id": {"type": "string"},
                "label_ids": {"type": "array", "items": {"type": "string"}},
                "estimate": {"type": "integer", "description": "Story point estimate"},
                "due_date": {"type": "string", "description": "ISO 8601 date"},
            },
            "required": ["title", "team_id"],
        },
    },
    {
        "name": "linear_update_issue",
        "description": "Update an existing Linear issue",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "integer"},
                "assignee_id": {"type": "string"},
                "state_id": {"type": "string"},
                "estimate": {"type": "integer"},
                "due_date": {"type": "string"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "name": "linear_list_teams",
        "description": "List all teams in the Linear workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "first": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "linear_list_projects",
        "description": "List Linear projects",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Filter by team ID"},
                "first": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "linear_add_comment",
        "description": "Add a comment to a Linear issue",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string"},
                "body": {"type": "string", "description": "Comment body (Markdown supported)"},
            },
            "required": ["issue_id", "body"],
        },
    },
]


async def _gql(query: str, variables: dict, token: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            LINEAR_GQL,
            json={"query": query, "variables": variables},
            headers={"Authorization": token, "Content-Type": "application/json"},
        )
        r.raise_for_status()
        return r.json()


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("LINEAR_API_KEY", "")
    if not token:
        return {"error": "LINEAR_API_KEY not configured"}

    if tool_name == "linear_list_issues":
        filter_parts: list[str] = []
        vars: dict[str, Any] = {"first": arguments.get("first", 50)}

        filter_conditions: dict[str, Any] = {}
        if arguments.get("team_id"):
            filter_conditions["team"] = {"id": {"eq": arguments["team_id"]}}
        if arguments.get("assignee_id"):
            filter_conditions["assignee"] = {"id": {"eq": arguments["assignee_id"]}}
        if arguments.get("state"):
            filter_conditions["state"] = {"name": {"eq": arguments["state"]}}
        if arguments.get("priority") is not None:
            filter_conditions["priority"] = {"eq": arguments["priority"]}

        query = """
        query ListIssues($first: Int, $filter: IssueFilter) {
          issues(first: $first, filter: $filter) {
            nodes {
              id
              identifier
              title
              description
              priority
              createdAt
              updatedAt
              state { name }
              assignee { name email }
              team { name }
            }
          }
        }
        """
        variables: dict[str, Any] = {"first": arguments.get("first", 50)}
        if filter_conditions:
            variables["filter"] = filter_conditions

        result = await _gql(query, variables, token)
        issues = result.get("data", {}).get("issues", {}).get("nodes", [])
        return {"issues": issues}

    elif tool_name == "linear_get_issue":
        query = """
        query GetIssue($id: String!) {
          issue(id: $id) {
            id
            identifier
            title
            description
            priority
            createdAt
            updatedAt
            state { name }
            assignee { name email }
            team { name }
            comments {
              nodes { id body createdAt user { name } }
            }
          }
        }
        """
        result = await _gql(query, {"id": arguments["issue_id"]}, token)
        return {"issue": result.get("data", {}).get("issue", {})}

    elif tool_name == "linear_create_issue":
        query = """
        mutation CreateIssue($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue {
              id
              identifier
              title
              url
            }
          }
        }
        """
        input_data: dict[str, Any] = {
            "title": arguments["title"],
            "teamId": arguments["team_id"],
        }
        if arguments.get("description"):
            input_data["description"] = arguments["description"]
        if arguments.get("priority") is not None:
            input_data["priority"] = arguments["priority"]
        if arguments.get("assignee_id"):
            input_data["assigneeId"] = arguments["assignee_id"]
        if arguments.get("state_id"):
            input_data["stateId"] = arguments["state_id"]
        if arguments.get("label_ids"):
            input_data["labelIds"] = arguments["label_ids"]
        if arguments.get("estimate") is not None:
            input_data["estimate"] = arguments["estimate"]
        if arguments.get("due_date"):
            input_data["dueDate"] = arguments["due_date"]

        result = await _gql(query, {"input": input_data}, token)
        create_result = result.get("data", {}).get("issueCreate", {})
        return {
            "success": create_result.get("success", False),
            "issue": create_result.get("issue", {}),
        }

    elif tool_name == "linear_update_issue":
        query = """
        mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success
            issue {
              id
              identifier
              title
              url
            }
          }
        }
        """
        input_data = {}
        for field, gql_field in [
            ("title", "title"),
            ("description", "description"),
            ("priority", "priority"),
            ("assignee_id", "assigneeId"),
            ("state_id", "stateId"),
            ("estimate", "estimate"),
            ("due_date", "dueDate"),
        ]:
            if field in arguments:
                input_data[gql_field] = arguments[field]

        result = await _gql(query, {"id": arguments["issue_id"], "input": input_data}, token)
        update_result = result.get("data", {}).get("issueUpdate", {})
        return {
            "success": update_result.get("success", False),
            "issue": update_result.get("issue", {}),
        }

    elif tool_name == "linear_list_teams":
        query = """
        query ListTeams($first: Int) {
          teams(first: $first) {
            nodes {
              id
              name
              key
              description
              timezone
            }
          }
        }
        """
        result = await _gql(query, {"first": arguments.get("first", 50)}, token)
        teams = result.get("data", {}).get("teams", {}).get("nodes", [])
        return {"teams": teams}

    elif tool_name == "linear_list_projects":
        if arguments.get("team_id"):
            query = """
            query ListProjects($teamId: String!, $first: Int) {
              team(id: $teamId) {
                projects(first: $first) {
                  nodes {
                    id
                    name
                    description
                    state
                    startDate
                    targetDate
                  }
                }
              }
            }
            """
            result = await _gql(
                query,
                {"teamId": arguments["team_id"], "first": arguments.get("first", 50)},
                token,
            )
            projects = (
                result.get("data", {})
                .get("team", {})
                .get("projects", {})
                .get("nodes", [])
            )
        else:
            query = """
            query ListProjects($first: Int) {
              projects(first: $first) {
                nodes {
                  id
                  name
                  description
                  state
                  startDate
                  targetDate
                }
              }
            }
            """
            result = await _gql(query, {"first": arguments.get("first", 50)}, token)
            projects = result.get("data", {}).get("projects", {}).get("nodes", [])
        return {"projects": projects}

    elif tool_name == "linear_add_comment":
        query = """
        mutation AddComment($input: CommentCreateInput!) {
          commentCreate(input: $input) {
            success
            comment {
              id
              body
              createdAt
            }
          }
        }
        """
        result = await _gql(
            query,
            {"input": {"issueId": arguments["issue_id"], "body": arguments["body"]}},
            token,
        )
        comment_result = result.get("data", {}).get("commentCreate", {})
        return {
            "success": comment_result.get("success", False),
            "comment": comment_result.get("comment", {}),
        }

    else:
        return {"error": f"Unknown tool: {tool_name}"}
