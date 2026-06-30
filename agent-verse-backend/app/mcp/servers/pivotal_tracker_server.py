"""Pivotal Tracker MCP server — agile stories, projects, and iterations.

Environment:
  PIVOTAL_TRACKER_TOKEN: Pivotal Tracker API token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://www.pivotaltracker.com/services/v5"

TOOL_DEFINITIONS = [
    {
        "name": "pivotal_tracker_list_stories",
        "description": "List stories in a Pivotal Tracker project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "story_type": {
                    "type": "string",
                    "enum": ["feature", "bug", "chore", "release"],
                },
                "current_state": {
                    "type": "string",
                    "enum": ["accepted", "delivered", "finished", "started", "rejected", "planned", "unstarted", "unscheduled"],
                },
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "pivotal_tracker_create_story",
        "description": "Create a new story in a Pivotal Tracker project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "story_type": {"type": "string", "enum": ["feature", "bug", "chore", "release"], "default": "feature"},
                "estimate": {"type": "number", "description": "Story point estimate"},
                "labels": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["project_id", "name"],
        },
    },
    {
        "name": "pivotal_tracker_update_story",
        "description": "Update an existing Pivotal Tracker story",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "story_id": {"type": "integer"},
                "name": {"type": "string"},
                "current_state": {"type": "string"},
                "estimate": {"type": "number"},
                "description": {"type": "string"},
            },
            "required": ["project_id", "story_id"],
        },
    },
    {
        "name": "pivotal_tracker_list_projects",
        "description": "List all Pivotal Tracker projects",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "pivotal_tracker_list_iterations",
        "description": "List iterations (sprints) for a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "scope": {
                    "type": "string",
                    "enum": ["current", "current_backlog", "backlog", "done", "done_current"],
                    "default": "current",
                },
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "pivotal_tracker_get_project_stats",
        "description": "Get statistics and velocity for a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
            },
            "required": ["project_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_token = os.getenv("PIVOTAL_TRACKER_TOKEN", "")
    if not api_token:
        return {"error": "PIVOTAL_TRACKER_TOKEN not configured"}

    headers = {
        "X-TrackerToken": api_token,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "pivotal_tracker_list_stories":
                pid = arguments["project_id"]
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 100),
                    "offset": arguments.get("offset", 0),
                }
                if st := arguments.get("story_type"):
                    params["story_type"] = st
                if cs := arguments.get("current_state"):
                    params["current_state"] = cs
                r = await c.get(f"{BASE}/projects/{pid}/stories", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pivotal_tracker_create_story":
                pid = arguments["project_id"]
                payload: dict[str, Any] = {
                    "name": arguments["name"],
                    "story_type": arguments.get("story_type", "feature"),
                }
                for field in ("description", "estimate"):
                    if v := arguments.get(field):
                        payload[field] = v
                if labels := arguments.get("labels"):
                    payload["labels"] = [{"name": lbl} for lbl in labels]
                r = await c.post(f"{BASE}/projects/{pid}/stories", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pivotal_tracker_update_story":
                pid = arguments["project_id"]
                sid = arguments["story_id"]
                payload = {}
                for field in ("name", "current_state", "estimate", "description"):
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.put(f"{BASE}/projects/{pid}/stories/{sid}", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pivotal_tracker_list_projects":
                r = await c.get(f"{BASE}/projects")
                r.raise_for_status()
                return r.json()

            elif tool_name == "pivotal_tracker_list_iterations":
                pid = arguments["project_id"]
                params = {"scope": arguments.get("scope", "current")}
                r = await c.get(f"{BASE}/projects/{pid}/iterations", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pivotal_tracker_get_project_stats":
                pid = arguments["project_id"]
                r = await c.get(f"{BASE}/projects/{pid}")
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "velocity_averaged_over": data.get("velocity_averaged_over"),
                    "current_iteration_number": data.get("current_iteration_number"),
                    "start_time": data.get("start_time"),
                    "initial_velocity": data.get("initial_velocity"),
                    "number_of_done_iterations_to_show": data.get("number_of_done_iterations_to_show"),
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("pivotal_tracker_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
