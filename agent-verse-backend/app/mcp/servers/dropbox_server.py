"""Dropbox MCP server — file and folder management via Dropbox API v2.

Environment variables:
  DROPBOX_ACCESS_TOKEN: Dropbox OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

DROPBOX_API = "https://api.dropboxapi.com/2"
DROPBOX_CONTENT = "https://content.dropboxapi.com/2"

TOOL_DEFINITIONS = [
    {
        "name": "dropbox_list_folder",
        "description": "List files and folders in a Dropbox path",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Dropbox path (use '' or '/' for root)",
                    "default": "",
                },
                "recursive": {"type": "boolean", "default": False},
                "include_deleted": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "dropbox_get_metadata",
        "description": "Get metadata for a file or folder in Dropbox",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "dropbox_download",
        "description": "Download a file from Dropbox (returns base64-encoded content)",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "dropbox_upload",
        "description": "Upload a file to Dropbox from base64-encoded content",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination path including filename"},
                "content_base64": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["add", "overwrite", "update"],
                    "default": "add",
                },
                "auto_rename": {"type": "boolean", "default": True},
            },
            "required": ["path", "content_base64"],
        },
    },
    {
        "name": "dropbox_delete",
        "description": "Delete a file or folder from Dropbox",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "dropbox_create_folder",
        "description": "Create a new folder in Dropbox",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "auto_rename": {"type": "boolean", "default": False},
            },
            "required": ["path"],
        },
    },
    {
        "name": "dropbox_search",
        "description": "Search for files and folders in Dropbox",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string", "default": "", "description": "Restrict search to this path"},
                "max_results": {"type": "integer", "default": 20},
                "file_status": {"type": "string", "enum": ["active", "deleted"], "default": "active"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "dropbox_share_link",
        "description": "Create a shareable link for a Dropbox file or folder",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "requested_visibility": {
                    "type": "string",
                    "enum": ["public", "team_only", "password"],
                    "default": "public",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "dropbox_move",
        "description": "Move or rename a file or folder in Dropbox",
        "parameters": {
            "type": "object",
            "properties": {
                "from_path": {"type": "string"},
                "to_path": {"type": "string"},
                "allow_shared_folder": {"type": "boolean", "default": False},
                "auto_rename": {"type": "boolean", "default": False},
            },
            "required": ["from_path", "to_path"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("DROPBOX_ACCESS_TOKEN", "")
    if not token:
        return {"error": "DROPBOX_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            if tool_name == "dropbox_list_folder":
                path = arguments.get("path", "")
                body: dict[str, Any] = {
                    "path": path,
                    "recursive": arguments.get("recursive", False),
                    "include_deleted": arguments.get("include_deleted", False),
                    "limit": arguments.get("limit", 100),
                }
                r = await c.post(
                    f"{DROPBOX_API}/files/list_folder",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "entries": [
                        {
                            ".tag": e[".tag"],
                            "name": e["name"],
                            "path_display": e.get("path_display", ""),
                            "id": e.get("id", ""),
                            "size": e.get("size", 0),
                        }
                        for e in data.get("entries", [])
                    ],
                    "has_more": data.get("has_more", False),
                    "cursor": data.get("cursor", ""),
                }

            elif tool_name == "dropbox_get_metadata":
                r = await c.post(
                    f"{DROPBOX_API}/files/get_metadata",
                    headers=hdrs,
                    json={"path": arguments["path"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "dropbox_download":
                import base64
                import json as json_mod

                download_hdrs = {
                    "Authorization": f"Bearer {token}",
                    "Dropbox-API-Arg": json_mod.dumps({"path": arguments["path"]}),
                }
                r = await c.post(
                    f"{DROPBOX_CONTENT}/files/download",
                    headers=download_hdrs,
                )
                r.raise_for_status()
                return {
                    "path": arguments["path"],
                    "size_bytes": len(r.content),
                    "content_base64": base64.b64encode(r.content).decode(),
                }

            elif tool_name == "dropbox_upload":
                import base64
                import json as json_mod

                content = base64.b64decode(arguments["content_base64"])
                upload_hdrs = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/octet-stream",
                    "Dropbox-API-Arg": json_mod.dumps(
                        {
                            "path": arguments["path"],
                            "mode": arguments.get("mode", "add"),
                            "autorename": arguments.get("auto_rename", True),
                        }
                    ),
                }
                r = await c.post(
                    f"{DROPBOX_CONTENT}/files/upload",
                    headers=upload_hdrs,
                    content=content,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "dropbox_delete":
                r = await c.post(
                    f"{DROPBOX_API}/files/delete_v2",
                    headers=hdrs,
                    json={"path": arguments["path"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "dropbox_create_folder":
                r = await c.post(
                    f"{DROPBOX_API}/files/create_folder_v2",
                    headers=hdrs,
                    json={
                        "path": arguments["path"],
                        "autorename": arguments.get("auto_rename", False),
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "dropbox_search":
                body = {
                    "query": arguments["query"],
                    "options": {
                        "max_results": arguments.get("max_results", 20),
                        "file_status": arguments.get("file_status", "active"),
                    },
                }
                if path := arguments.get("path", ""):
                    body["options"]["path"] = path
                r = await c.post(
                    f"{DROPBOX_API}/files/search_v2",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "matches": [
                        {
                            "path": m.get("metadata", {}).get("metadata", {}).get("path_display", ""),
                            "name": m.get("metadata", {}).get("metadata", {}).get("name", ""),
                            "type": m.get("metadata", {}).get(".tag", ""),
                        }
                        for m in data.get("matches", [])
                    ],
                    "has_more": data.get("has_more", False),
                }

            elif tool_name == "dropbox_share_link":
                body = {
                    "path": arguments["path"],
                    "settings": {
                        "requested_visibility": {
                            ".tag": arguments.get("requested_visibility", "public")
                        }
                    },
                }
                r = await c.post(
                    f"{DROPBOX_API}/sharing/create_shared_link_with_settings",
                    headers=hdrs,
                    json=body,
                )
                if r.status_code == 409:
                    # Link already exists — fetch it
                    data = r.json()
                    if shared_link := data.get("error", {}).get("shared_link_already_exists", {}).get("metadata", {}):
                        return {"url": shared_link.get("url", "")}
                r.raise_for_status()
                return r.json()

            elif tool_name == "dropbox_move":
                r = await c.post(
                    f"{DROPBOX_API}/files/move_v2",
                    headers=hdrs,
                    json={
                        "from_path": arguments["from_path"],
                        "to_path": arguments["to_path"],
                        "allow_shared_folder": arguments.get("allow_shared_folder", False),
                        "autorename": arguments.get("auto_rename", False),
                    },
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("dropbox_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
