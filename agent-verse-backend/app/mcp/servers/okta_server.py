"""Okta MCP server — user lifecycle, groups, and application management.

Environment variables:
  OKTA_BASE_URL: Okta tenant base URL, e.g. https://dev-123456.okta.com
  OKTA_API_TOKEN: Okta SSWS API token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "okta_list_users",
        "description": "List Okta users. Supports search and pagination.",
        "parameters": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Okta search expression, e.g. 'profile.firstName eq \"John\"'",
                },
                "filter": {
                    "type": "string",
                    "description": "SCIM filter expression, e.g. 'status eq \"ACTIVE\"'",
                },
                "limit": {"type": "integer", "default": 50},
                "after": {
                    "type": "string",
                    "description": "Cursor for next page (value of 'after' link header)",
                },
            },
        },
    },
    {
        "name": "okta_get_user",
        "description": "Get a single Okta user by ID, login, or email.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "User ID, login, or email address",
                },
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "okta_create_user",
        "description": "Create a new Okta user. Pass activate=true to activate immediately.",
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "email": {"type": "string", "description": "Primary email and login"},
                "login": {
                    "type": "string",
                    "description": "Okta login (defaults to email if omitted)",
                },
                "mobile_phone": {"type": "string"},
                "password": {
                    "type": "string",
                    "description": "Initial password (sent to the user on activation)",
                },
                "activate": {
                    "type": "boolean",
                    "default": True,
                    "description": "Activate user immediately after creation",
                },
                "group_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Group IDs to add the user to on creation",
                },
            },
            "required": ["first_name", "last_name", "email"],
        },
    },
    {
        "name": "okta_update_user",
        "description": "Update profile fields of an existing Okta user.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User ID or login"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "email": {"type": "string"},
                "mobile_phone": {"type": "string"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "okta_deactivate_user",
        "description": "Deactivate an Okta user (disables login, preserves data).",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User ID or login"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "okta_list_groups",
        "description": "List Okta groups, optionally filtering by name.",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search groups by name"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "okta_add_user_to_group",
        "description": "Add an Okta user to a group.",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string"},
                "user_id": {"type": "string"},
            },
            "required": ["group_id", "user_id"],
        },
    },
    {
        "name": "okta_list_applications",
        "description": "List Okta applications (integrations) in the tenant.",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Filter apps by name"},
                "limit": {"type": "integer", "default": 50},
                "filter": {
                    "type": "string",
                    "description": "SCIM filter, e.g. 'status eq \"ACTIVE\"'",
                },
            },
        },
    },
    {
        "name": "okta_assign_user_to_app",
        "description": "Assign a user to an Okta application.",
        "parameters": {
            "type": "object",
            "properties": {
                "app_id": {"type": "string", "description": "Application ID"},
                "user_id": {"type": "string", "description": "User ID"},
                "scope": {
                    "type": "string",
                    "enum": ["USER", "GROUP"],
                    "default": "USER",
                },
            },
            "required": ["app_id", "user_id"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("OKTA_API_TOKEN", "")
    return {
        "Authorization": f"SSWS {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    base_url = os.getenv("OKTA_BASE_URL", "").rstrip("/")
    if not base_url:
        return {"error": "OKTA_BASE_URL not configured"}
    api_token = os.getenv("OKTA_API_TOKEN", "")
    if not api_token:
        return {"error": "OKTA_API_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=base_url, headers=_headers(), timeout=30.0
        ) as client:
            if tool_name == "okta_list_users":
                params: dict[str, Any] = {"limit": arguments.get("limit", 50)}
                if arguments.get("search"):
                    params["search"] = arguments["search"]
                if arguments.get("filter"):
                    params["filter"] = arguments["filter"]
                if arguments.get("after"):
                    params["after"] = arguments["after"]
                resp = await client.get("/api/v1/users", params=params)
                resp.raise_for_status()
                users = resp.json()
                return {
                    "count": len(users),
                    "users": [
                        {
                            "id": u["id"],
                            "status": u.get("status", ""),
                            "login": (u.get("profile") or {}).get("login", ""),
                            "email": (u.get("profile") or {}).get("email", ""),
                            "first_name": (u.get("profile") or {}).get("firstName", ""),
                            "last_name": (u.get("profile") or {}).get("lastName", ""),
                            "created": u.get("created", ""),
                            "last_login": u.get("lastLogin", ""),
                        }
                        for u in users
                    ],
                }

            elif tool_name == "okta_get_user":
                resp = await client.get(f"/api/v1/users/{arguments['user_id']}")
                resp.raise_for_status()
                u = resp.json()
                profile = u.get("profile") or {}
                return {
                    "id": u["id"],
                    "status": u.get("status", ""),
                    "login": profile.get("login", ""),
                    "email": profile.get("email", ""),
                    "first_name": profile.get("firstName", ""),
                    "last_name": profile.get("lastName", ""),
                    "mobile_phone": profile.get("mobilePhone", ""),
                    "created": u.get("created", ""),
                    "last_login": u.get("lastLogin", ""),
                    "password_changed": u.get("passwordChanged", ""),
                }

            elif tool_name == "okta_create_user":
                profile: dict[str, Any] = {
                    "firstName": arguments["first_name"],
                    "lastName": arguments["last_name"],
                    "email": arguments["email"],
                    "login": arguments.get("login", arguments["email"]),
                }
                if arguments.get("mobile_phone"):
                    profile["mobilePhone"] = arguments["mobile_phone"]

                payload: dict[str, Any] = {"profile": profile}
                if arguments.get("password"):
                    payload["credentials"] = {
                        "password": {"value": arguments["password"]}
                    }
                if arguments.get("group_ids"):
                    payload["groupIds"] = arguments["group_ids"]

                params = {"activate": str(arguments.get("activate", True)).lower()}
                resp = await client.post("/api/v1/users", json=payload, params=params)
                resp.raise_for_status()
                u = resp.json()
                return {
                    "id": u["id"],
                    "status": u.get("status", ""),
                    "login": (u.get("profile") or {}).get("login", ""),
                }

            elif tool_name == "okta_update_user":
                user_id = arguments["user_id"]
                profile = {}
                for src, dst in [
                    ("first_name", "firstName"),
                    ("last_name", "lastName"),
                    ("email", "email"),
                    ("mobile_phone", "mobilePhone"),
                ]:
                    if src in arguments:
                        profile[dst] = arguments[src]
                resp = await client.post(
                    f"/api/v1/users/{user_id}", json={"profile": profile}
                )
                resp.raise_for_status()
                u = resp.json()
                return {
                    "id": u["id"],
                    "status": u.get("status", ""),
                    "login": (u.get("profile") or {}).get("login", ""),
                    "updated": True,
                }

            elif tool_name == "okta_deactivate_user":
                user_id = arguments["user_id"]
                resp = await client.post(
                    f"/api/v1/users/{user_id}/lifecycle/deactivate"
                )
                resp.raise_for_status()
                return {"deactivated": True, "user_id": user_id}

            elif tool_name == "okta_list_groups":
                params = {"limit": arguments.get("limit", 50)}
                if arguments.get("q"):
                    params["q"] = arguments["q"]
                resp = await client.get("/api/v1/groups", params=params)
                resp.raise_for_status()
                groups = resp.json()
                return {
                    "count": len(groups),
                    "groups": [
                        {
                            "id": g["id"],
                            "type": g.get("type", ""),
                            "name": (g.get("profile") or {}).get("name", ""),
                            "description": (g.get("profile") or {}).get("description", ""),
                            "created": g.get("created", ""),
                        }
                        for g in groups
                    ],
                }

            elif tool_name == "okta_add_user_to_group":
                group_id = arguments["group_id"]
                user_id = arguments["user_id"]
                resp = await client.put(f"/api/v1/groups/{group_id}/users/{user_id}")
                resp.raise_for_status()
                return {"added": True, "group_id": group_id, "user_id": user_id}

            elif tool_name == "okta_list_applications":
                params = {"limit": arguments.get("limit", 50)}
                if arguments.get("q"):
                    params["q"] = arguments["q"]
                if arguments.get("filter"):
                    params["filter"] = arguments["filter"]
                resp = await client.get("/api/v1/apps", params=params)
                resp.raise_for_status()
                apps = resp.json()
                return {
                    "count": len(apps),
                    "applications": [
                        {
                            "id": a["id"],
                            "name": a.get("name", ""),
                            "label": a.get("label", ""),
                            "status": a.get("status", ""),
                            "sign_on_mode": a.get("signOnMode", ""),
                            "created": a.get("created", ""),
                        }
                        for a in apps
                    ],
                }

            elif tool_name == "okta_assign_user_to_app":
                app_id = arguments["app_id"]
                user_id = arguments["user_id"]
                payload = {
                    "id": user_id,
                    "scope": arguments.get("scope", "USER"),
                }
                resp = await client.post(f"/api/v1/apps/{app_id}/users", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return {
                    "assigned": True,
                    "app_id": app_id,
                    "user_id": data.get("id", user_id),
                    "status": data.get("status", ""),
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
