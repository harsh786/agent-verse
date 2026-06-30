"""Thinkific MCP server — Thinkific online courses, users, enrollments, and stats.

Environment:
  THINKIFIC_API_KEY: Thinkific API key from Settings > Developer
  THINKIFIC_SUBDOMAIN: Thinkific school subdomain (e.g. myschool)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.thinkific.com/api/public/v1"

TOOL_DEFINITIONS = [
    {
        "name": "thinkific_list_courses",
        "description": "List courses in the Thinkific school",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number for pagination", "default": 1},
                "limit": {"type": "integer", "description": "Number of courses per page (max 250)", "default": 20},
            },
        },
    },
    {
        "name": "thinkific_list_users",
        "description": "List users (students) in the Thinkific school",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Filter by email address"},
                "page": {"type": "integer", "description": "Page number", "default": 1},
                "limit": {"type": "integer", "description": "Number of users per page", "default": 20},
            },
        },
    },
    {
        "name": "thinkific_create_user",
        "description": "Create a new user in the Thinkific school",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "User email address"},
                "first_name": {"type": "string", "description": "User first name"},
                "last_name": {"type": "string", "description": "User last name"},
                "password": {"type": "string", "description": "Initial password for the user"},
                "roles": {"type": "array", "items": {"type": "string"}, "description": "User roles: student, affiliate"},
            },
            "required": ["email", "first_name", "last_name"],
        },
    },
    {
        "name": "thinkific_enroll_user",
        "description": "Enroll a user in a Thinkific course",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "Thinkific user ID"},
                "course_id": {"type": "integer", "description": "Thinkific course ID"},
                "activated_at": {"type": "string", "description": "Enrollment activation date (ISO 8601)"},
                "expiry_date": {"type": "string", "description": "Enrollment expiry date (ISO 8601)"},
            },
            "required": ["user_id", "course_id"],
        },
    },
    {
        "name": "thinkific_list_enrollments",
        "description": "List course enrollments in the Thinkific school",
        "parameters": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "description": "Filter by course ID"},
                "user_id": {"type": "integer", "description": "Filter by user ID"},
                "page": {"type": "integer", "description": "Page number", "default": 1},
                "limit": {"type": "integer", "description": "Number of enrollments per page", "default": 20},
            },
        },
    },
    {
        "name": "thinkific_get_course_stats",
        "description": "Get statistics for a specific Thinkific course",
        "parameters": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "description": "Thinkific course ID"},
            },
            "required": ["course_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("THINKIFIC_API_KEY", "")
    subdomain = os.getenv("THINKIFIC_SUBDOMAIN", "")
    if not api_key:
        return {"error": "THINKIFIC_API_KEY not configured"}
    if not subdomain:
        return {"error": "THINKIFIC_SUBDOMAIN not configured"}

    headers = {
        "X-Auth-API-Key": api_key,
        "X-Auth-Subdomain": subdomain,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "thinkific_list_courses":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "limit": arguments.get("limit", 20),
                }
                r = await client.get(f"{BASE}/courses", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "courses": [
                        {
                            "id": c.get("id"),
                            "name": c.get("name"),
                            "slug": c.get("slug"),
                            "product_id": c.get("product_id"),
                            "published": c.get("published"),
                        }
                        for c in data.get("items", [])
                    ],
                    "meta": data.get("meta", {}),
                }

            elif tool_name == "thinkific_list_users":
                params = {
                    "page": arguments.get("page", 1),
                    "limit": arguments.get("limit", 20),
                }
                if "email" in arguments:
                    params["email"] = arguments["email"]
                r = await client.get(f"{BASE}/users", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "users": [
                        {
                            "id": u.get("id"),
                            "email": u.get("email"),
                            "first_name": u.get("first_name"),
                            "last_name": u.get("last_name"),
                            "created_at": u.get("created_at"),
                        }
                        for u in data.get("items", [])
                    ],
                    "meta": data.get("meta", {}),
                }

            elif tool_name == "thinkific_create_user":
                payload: dict[str, Any] = {
                    "email": arguments["email"],
                    "first_name": arguments["first_name"],
                    "last_name": arguments["last_name"],
                }
                for field in ("password", "roles"):
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await client.post(f"{BASE}/users", headers=headers, json=payload)
                r.raise_for_status()
                user = r.json()
                return {
                    "id": user.get("id"),
                    "email": user.get("email"),
                    "first_name": user.get("first_name"),
                    "last_name": user.get("last_name"),
                }

            elif tool_name == "thinkific_enroll_user":
                payload = {
                    "user_id": arguments["user_id"],
                    "course_id": arguments["course_id"],
                }
                if "activated_at" in arguments:
                    payload["activated_at"] = arguments["activated_at"]
                if "expiry_date" in arguments:
                    payload["expiry_date"] = arguments["expiry_date"]
                r = await client.post(f"{BASE}/enrollments", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "thinkific_list_enrollments":
                params = {
                    "page": arguments.get("page", 1),
                    "limit": arguments.get("limit", 20),
                }
                if "course_id" in arguments:
                    params["course_id"] = arguments["course_id"]
                if "user_id" in arguments:
                    params["user_id"] = arguments["user_id"]
                r = await client.get(f"{BASE}/enrollments", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "enrollments": data.get("items", []),
                    "meta": data.get("meta", {}),
                }

            elif tool_name == "thinkific_get_course_stats":
                r = await client.get(
                    f"{BASE}/courses/{arguments['course_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "user_count": data.get("user_count"),
                    "review_count": data.get("review_count"),
                    "reviews_rating": data.get("reviews_rating"),
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("thinkific_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
