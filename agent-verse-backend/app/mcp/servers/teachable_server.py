"""Teachable MCP server — Teachable online course users, enrollments, coupons, and progress.

Environment:
  TEACHABLE_API_KEY: Teachable API key from Settings > Integrations
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://developers.teachable.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "teachable_list_courses",
        "description": "List all courses in the Teachable school",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number for pagination", "default": 1},
                "per": {"type": "integer", "description": "Number of results per page (max 50)", "default": 20},
            },
        },
    },
    {
        "name": "teachable_list_users",
        "description": "List users (students) in the Teachable school",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Filter users by email address"},
                "page": {"type": "integer", "description": "Page number", "default": 1},
                "per": {"type": "integer", "description": "Number of results per page (max 50)", "default": 20},
            },
        },
    },
    {
        "name": "teachable_enroll_user",
        "description": "Enroll a user in a Teachable course",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "Teachable user ID"},
                "course_id": {"type": "integer", "description": "Teachable course ID"},
            },
            "required": ["user_id", "course_id"],
        },
    },
    {
        "name": "teachable_get_user_progress",
        "description": "Get a user's progress in a specific Teachable course",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "Teachable user ID"},
                "course_id": {"type": "integer", "description": "Teachable course ID"},
            },
            "required": ["user_id", "course_id"],
        },
    },
    {
        "name": "teachable_list_coupons",
        "description": "List discount coupons for a Teachable course",
        "parameters": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "description": "Teachable course ID to list coupons for"},
                "page": {"type": "integer", "description": "Page number", "default": 1},
                "per": {"type": "integer", "description": "Number of results per page", "default": 20},
            },
            "required": ["course_id"],
        },
    },
    {
        "name": "teachable_create_coupon",
        "description": "Create a discount coupon for a Teachable course",
        "parameters": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "description": "Teachable course ID"},
                "name": {"type": "string", "description": "Coupon display name"},
                "code": {"type": "string", "description": "Coupon code (unique)"},
                "discount_type": {"type": "string", "description": "Discount type: percent or amount"},
                "amount_off": {"type": "integer", "description": "Discount amount (percentage or cents)"},
                "max_uses": {"type": "integer", "description": "Maximum number of times coupon can be used"},
            },
            "required": ["course_id", "name", "code", "discount_type", "amount_off"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("TEACHABLE_API_KEY", "")
    if not api_key:
        return {"error": "TEACHABLE_API_KEY not configured"}

    headers = {
        "apiKey": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "teachable_list_courses":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per": arguments.get("per", 20),
                }
                r = await client.get(f"{BASE}/courses", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "courses": [
                        {
                            "id": c.get("id"),
                            "name": c.get("name"),
                            "heading": c.get("heading"),
                            "is_published": c.get("is_published"),
                            "price": c.get("price"),
                        }
                        for c in data.get("courses", [])
                    ],
                    "meta": data.get("meta", {}),
                }

            elif tool_name == "teachable_list_users":
                params = {
                    "page": arguments.get("page", 1),
                    "per": arguments.get("per", 20),
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
                            "name": u.get("name"),
                            "role": u.get("role"),
                        }
                        for u in data.get("users", [])
                    ],
                    "meta": data.get("meta", {}),
                }

            elif tool_name == "teachable_enroll_user":
                r = await client.post(
                    f"{BASE}/users/{arguments['user_id']}/enrollments",
                    headers=headers,
                    json={"course_id": arguments["course_id"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "teachable_get_user_progress":
                r = await client.get(
                    f"{BASE}/users/{arguments['user_id']}/report_cards/{arguments['course_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "teachable_list_coupons":
                params = {
                    "page": arguments.get("page", 1),
                    "per": arguments.get("per", 20),
                }
                r = await client.get(
                    f"{BASE}/courses/{arguments['course_id']}/coupons",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "coupons": data.get("coupons", []),
                    "meta": data.get("meta", {}),
                }

            elif tool_name == "teachable_create_coupon":
                payload: dict[str, Any] = {
                    "name": arguments["name"],
                    "code": arguments["code"],
                    "discount_type": arguments["discount_type"],
                    "amount_off": arguments["amount_off"],
                }
                if "max_uses" in arguments:
                    payload["max_uses"] = arguments["max_uses"]
                r = await client.post(
                    f"{BASE}/courses/{arguments['course_id']}/coupons",
                    headers=headers,
                    json={"coupon": payload},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("teachable_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
