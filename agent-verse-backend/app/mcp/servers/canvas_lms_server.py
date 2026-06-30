"""Canvas LMS MCP server — course management, assignments, and student grading.

Environment:
  CANVAS_ACCESS_TOKEN: Canvas LMS API token
  CANVAS_DOMAIN: Canvas instance domain (e.g. myschool.instructure.com)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base_url() -> str:
    domain = os.getenv("CANVAS_DOMAIN", "canvas.instructure.com")
    return f"https://{domain}/api/v1"


TOOL_DEFINITIONS = [
    {
        "name": "canvas_list_courses",
        "description": "List courses in Canvas for the authenticated user or account",
        "parameters": {
            "type": "object",
            "properties": {
                "enrollment_type": {"type": "string", "description": "Filter by role: teacher, student, ta"},
                "include": {"type": "string", "description": "Comma-separated extra fields to include"},
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
        },
    },
    {
        "name": "canvas_list_students",
        "description": "List students enrolled in a Canvas course",
        "parameters": {
            "type": "object",
            "properties": {
                "course_id": {"type": "string", "description": "Canvas course ID"},
                "include": {"type": "string", "description": "Additional fields to include"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["course_id"],
        },
    },
    {
        "name": "canvas_create_assignment",
        "description": "Create a new assignment in a Canvas course",
        "parameters": {
            "type": "object",
            "properties": {
                "course_id": {"type": "string", "description": "Canvas course ID"},
                "name": {"type": "string", "description": "Assignment title"},
                "description": {"type": "string", "description": "Assignment instructions (HTML)"},
                "due_at": {"type": "string", "description": "Due date in ISO 8601 format"},
                "points_possible": {"type": "number", "description": "Maximum points for the assignment"},
                "submission_types": {
                    "type": "array",
                    "description": "Allowed submission types",
                    "items": {"type": "string"},
                },
            },
            "required": ["course_id", "name"],
        },
    },
    {
        "name": "canvas_list_submissions",
        "description": "List student submissions for a Canvas assignment",
        "parameters": {
            "type": "object",
            "properties": {
                "course_id": {"type": "string", "description": "Canvas course ID"},
                "assignment_id": {"type": "string", "description": "Assignment ID"},
                "include": {"type": "string", "description": "Extra data to include (e.g. user)"},
                "per_page": {"type": "integer", "description": "Submissions per page"},
            },
            "required": ["course_id", "assignment_id"],
        },
    },
    {
        "name": "canvas_grade_submission",
        "description": "Grade a student submission for an assignment in Canvas",
        "parameters": {
            "type": "object",
            "properties": {
                "course_id": {"type": "string", "description": "Canvas course ID"},
                "assignment_id": {"type": "string", "description": "Assignment ID"},
                "student_id": {"type": "string", "description": "Student user ID"},
                "grade": {"type": "string", "description": "Grade to assign (numeric or letter)"},
                "text_comment": {"type": "string", "description": "Feedback comment for the student"},
            },
            "required": ["course_id", "assignment_id", "student_id", "grade"],
        },
    },
    {
        "name": "canvas_announce_to_course",
        "description": "Post an announcement to all students in a Canvas course",
        "parameters": {
            "type": "object",
            "properties": {
                "course_id": {"type": "string", "description": "Canvas course ID"},
                "title": {"type": "string", "description": "Announcement title"},
                "message": {"type": "string", "description": "Announcement message body (HTML allowed)"},
                "delayed_post_at": {"type": "string", "description": "Optional scheduled post time"},
            },
            "required": ["course_id", "title", "message"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("CANVAS_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "CANVAS_ACCESS_TOKEN not configured"}
    if not os.getenv("CANVAS_DOMAIN", ""):
        return {"error": "CANVAS_DOMAIN not configured"}

    base_url = _base_url()
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "canvas_list_courses":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{base_url}/courses", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "canvas_list_students":
                course_id = arguments["course_id"]
                params = {k: v for k, v in arguments.items() if k != "course_id" and v is not None}
                params["enrollment_type[]"] = "student"
                r = await client.get(
                    f"{base_url}/courses/{course_id}/users",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "canvas_create_assignment":
                course_id = arguments["course_id"]
                payload: dict[str, Any] = {
                    "assignment[name]": arguments["name"],
                }
                if "description" in arguments:
                    payload["assignment[description]"] = arguments["description"]
                if "due_at" in arguments:
                    payload["assignment[due_at]"] = arguments["due_at"]
                if "points_possible" in arguments:
                    payload["assignment[points_possible]"] = arguments["points_possible"]
                if "submission_types" in arguments:
                    for i, st in enumerate(arguments["submission_types"]):
                        payload[f"assignment[submission_types][]"] = st
                r = await client.post(
                    f"{base_url}/courses/{course_id}/assignments",
                    headers=headers,
                    data=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "canvas_list_submissions":
                course_id = arguments["course_id"]
                assignment_id = arguments["assignment_id"]
                params = {k: v for k, v in arguments.items() if k not in ("course_id", "assignment_id") and v is not None}
                r = await client.get(
                    f"{base_url}/courses/{course_id}/assignments/{assignment_id}/submissions",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "canvas_grade_submission":
                course_id = arguments["course_id"]
                assignment_id = arguments["assignment_id"]
                student_id = arguments["student_id"]
                payload = {"submission[posted_grade]": arguments["grade"]}
                if "text_comment" in arguments:
                    payload["comment[text_comment]"] = arguments["text_comment"]
                r = await client.put(
                    f"{base_url}/courses/{course_id}/assignments/{assignment_id}/submissions/{student_id}",
                    headers=headers,
                    data=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "canvas_announce_to_course":
                course_id = arguments["course_id"]
                payload = {
                    "title": arguments["title"],
                    "message": arguments["message"],
                    "is_announcement": True,
                }
                if "delayed_post_at" in arguments:
                    payload["delayed_post_at"] = arguments["delayed_post_at"]
                r = await client.post(
                    f"{base_url}/courses/{course_id}/discussion_topics",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
