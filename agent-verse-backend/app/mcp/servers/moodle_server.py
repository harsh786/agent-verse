"""Moodle MCP server — e-learning course management, users, and assignments.

Environment:
  MOODLE_TOKEN: Moodle web service token
  MOODLE_URL: Base URL of the Moodle instance (e.g. https://mymoodle.example.com)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base_url() -> str:
    url = os.getenv("MOODLE_URL", "")
    return f"{url.rstrip('/')}/webservice/rest/server.php" if url else ""


TOOL_DEFINITIONS = [
    {
        "name": "moodle_list_courses",
        "description": "List all courses available in the Moodle instance",
        "parameters": {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "description": "Optional list of specific course IDs to retrieve",
                    "items": {"type": "integer"},
                },
            },
        },
    },
    {
        "name": "moodle_get_course",
        "description": "Get detailed information about a specific Moodle course",
        "parameters": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "description": "Moodle course ID"},
            },
            "required": ["course_id"],
        },
    },
    {
        "name": "moodle_list_users",
        "description": "Search for users in Moodle by various criteria",
        "parameters": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "description": "Search field: username, email, firstname, lastname"},
                "value": {"type": "string", "description": "Search value"},
            },
        },
    },
    {
        "name": "moodle_create_user",
        "description": "Create a new user account in Moodle",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Login username"},
                "password": {"type": "string", "description": "Initial password"},
                "firstname": {"type": "string", "description": "First name"},
                "lastname": {"type": "string", "description": "Last name"},
                "email": {"type": "string", "description": "Email address"},
                "auth": {"type": "string", "description": "Auth method (default: manual)"},
            },
            "required": ["username", "password", "firstname", "lastname", "email"],
        },
    },
    {
        "name": "moodle_enroll_user",
        "description": "Enroll a user in a Moodle course with a specified role",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "Moodle user ID"},
                "course_id": {"type": "integer", "description": "Course ID to enroll into"},
                "role_id": {"type": "integer", "description": "Role ID (5=student, 3=teacher)"},
            },
            "required": ["user_id", "course_id"],
        },
    },
    {
        "name": "moodle_list_assignments",
        "description": "List assignments for specified courses",
        "parameters": {
            "type": "object",
            "properties": {
                "course_ids": {
                    "type": "array",
                    "description": "List of course IDs to get assignments for",
                    "items": {"type": "integer"},
                },
            },
            "required": ["course_ids"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    token = os.getenv("MOODLE_TOKEN", "")
    moodle_url = os.getenv("MOODLE_URL", "")
    if not token:
        return {"error": "MOODLE_TOKEN not configured"}
    if not moodle_url:
        return {"error": "MOODLE_URL not configured"}

    endpoint = _base_url()
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            def _params(wsfunction: str, extra: dict[str, Any]) -> dict[str, Any]:
                return {
                    "wstoken": token,
                    "moodlewsrestformat": "json",
                    "wsfunction": wsfunction,
                    **extra,
                }

            if tool_name == "moodle_list_courses":
                params = _params("core_course_get_courses", {})
                if "ids" in arguments:
                    for i, cid in enumerate(arguments["ids"]):
                        params[f"options[ids][{i}]"] = cid
                r = await client.get(endpoint, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "moodle_get_course":
                params = _params("core_course_get_courses", {f"options[ids][0]": arguments["course_id"]})
                r = await client.get(endpoint, params=params)
                r.raise_for_status()
                data = r.json()
                return data[0] if isinstance(data, list) and data else data

            if tool_name == "moodle_list_users":
                params = _params("core_user_get_users", {
                    "criteria[0][key]": arguments.get("field", "email"),
                    "criteria[0][value]": arguments.get("value", "%"),
                })
                r = await client.get(endpoint, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "moodle_create_user":
                params = _params("core_user_create_users", {
                    "users[0][username]": arguments["username"],
                    "users[0][password]": arguments["password"],
                    "users[0][firstname]": arguments["firstname"],
                    "users[0][lastname]": arguments["lastname"],
                    "users[0][email]": arguments["email"],
                    "users[0][auth]": arguments.get("auth", "manual"),
                })
                r = await client.post(endpoint, data=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "moodle_enroll_user":
                params = _params("enrol_manual_enrol_users", {
                    "enrolments[0][userid]": arguments["user_id"],
                    "enrolments[0][courseid]": arguments["course_id"],
                    "enrolments[0][roleid]": arguments.get("role_id", 5),
                })
                r = await client.post(endpoint, data=params)
                r.raise_for_status()
                return r.json() if r.content else {"enrolled": True}

            if tool_name == "moodle_list_assignments":
                params = _params("mod_assign_get_assignments", {})
                for i, cid in enumerate(arguments["course_ids"]):
                    params[f"courseids[{i}]"] = cid
                r = await client.get(endpoint, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
